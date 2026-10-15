/*
 * Mesh CSI experiment (P3a).
 *
 * Every node broadcasts a small ESP-NOW frame at a fixed HT20 MCS0 rate. Every
 * other node receives it and gets CSI for that sender, so each node measures
 * one link per peer. Per-peer features are reported once per second on the
 * serial port as machine-readable "L," lines.
 */
#include <math.h>
#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_now.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include "nvs_flash.h"

#define TAG "mesh"

#define MESH_CHANNEL 7  // used only when no Wi-Fi credentials are compiled in
#define UDP_PORT 5005
#define TX_PERIOD_MS 20
#define REPORT_PERIOD_MS 1000
#define MAX_PEERS 8
#define MAX_SUBC 192
#define MAGIC 0x4D43u

typedef struct __attribute__((packed)) {
    uint16_t magic;
    uint32_t seq;
    uint32_t tx_ms;
} mesh_frame_t;

typedef struct {
    bool used;
    uint8_t mac[6];
    uint32_t esp_now_rx;
    uint32_t csi_n;
    uint32_t csi_len;
    double rssi_sum;
    double turb_mean;
    double turb_m2;
    double diff_sum;
    uint32_t diff_n;
    float prev[MAX_SUBC];
    float sub_mean[MAX_SUBC];
    float sub_m2[MAX_SUBC];
    double amp_sum;
    uint16_t prev_n;
    uint32_t last_seq;
    uint32_t lost;
} peer_t;

static peer_t s_peers[MAX_PEERS];
static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;
static const uint8_t BCAST[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};
static uint8_t s_self[6];
static volatile uint32_t s_tx_ok, s_tx_fail;
static volatile bool s_got_ip;
static int s_sock = -1;
static struct sockaddr_in s_dest;

static peer_t *find_peer(const uint8_t *mac, bool create) {
    for (int i = 0; i < MAX_PEERS; i++) {
        if (s_peers[i].used && memcmp(s_peers[i].mac, mac, 6) == 0) return &s_peers[i];
    }
    if (!create) return NULL;
    for (int i = 0; i < MAX_PEERS; i++) {
        if (!s_peers[i].used) {
            memset(&s_peers[i], 0, sizeof(peer_t));
            s_peers[i].used = true;
            memcpy(s_peers[i].mac, mac, 6);
            return &s_peers[i];
        }
    }
    return NULL;
}

static void on_recv(const esp_now_recv_info_t *info, const uint8_t *data, int len) {
    if (len < (int)sizeof(mesh_frame_t)) return;
    mesh_frame_t f;
    memcpy(&f, data, sizeof(f));
    if (f.magic != MAGIC) return;
    portENTER_CRITICAL(&s_lock);
    peer_t *p = find_peer(info->src_addr, true);
    if (p) {
        if (p->esp_now_rx > 0 && f.seq > p->last_seq + 1) p->lost += f.seq - p->last_seq - 1;
        p->last_seq = f.seq;
        p->esp_now_rx++;
    }
    portEXIT_CRITICAL(&s_lock);
}

static void on_sent(const esp_now_send_info_t *info, esp_now_send_status_t status) {
    (void)info;
    if (status == ESP_NOW_SEND_SUCCESS) s_tx_ok++; else s_tx_fail++;
}

static void on_csi(void *ctx, wifi_csi_info_t *csi) {
    (void)ctx;
    if (!csi || !csi->buf || csi->len < 8) return;
    // Only broadcast HT frames from known mesh peers (registered by the ESP-NOW rx callback).
    if (csi->rx_ctrl.sig_mode != 1 || memcmp(csi->dmac, BCAST, 6) != 0) return;

    float amp[MAX_SUBC];
    int start = csi->first_word_invalid ? 4 : 0;
    int n = 0;
    for (int i = start; i + 1 < csi->len && n < MAX_SUBC; i += 2) {
        float im = csi->buf[i], re = csi->buf[i + 1];
        amp[n++] = sqrtf(re * re + im * im);
    }
    if (n < 8) return;

    double sum = 0;
    int nz = 0;
    for (int i = 0; i < n; i++) {
        if (amp[i] > 0.5f) { sum += amp[i]; nz++; }
    }
    if (nz < 8) return;
    double mean = sum / nz, var = 0;
    for (int i = 0; i < n; i++) {
        if (amp[i] > 0.5f) var += (amp[i] / mean - 1.0) * (amp[i] / mean - 1.0);
    }
    double turb = sqrt(var / nz);  // gain-independent spatial spread of the normalised amplitudes

    portENTER_CRITICAL_ISR(&s_lock);
    peer_t *p = find_peer(csi->mac, false);
    if (p) {
        p->csi_n++;
        p->csi_len = csi->len;
        p->rssi_sum += csi->rx_ctrl.rssi;
        double d = turb - p->turb_mean;
        p->turb_mean += d / p->csi_n;
        p->turb_m2 += d * (turb - p->turb_mean);
        if (p->prev_n == n) {
            double diff = 0;
            for (int i = 0; i < n; i++) {
                float a = amp[i] / (float)mean;
                diff += fabsf(a - p->prev[i]);
            }
            p->diff_sum += diff / n;
            p->diff_n++;
        }
        for (int i = 0; i < n; i++) {
            float a = amp[i] / (float)mean;
            float dd = a - p->sub_mean[i];
            p->sub_mean[i] += dd / p->csi_n;
            p->sub_m2[i] += dd * (a - p->sub_mean[i]);
            p->prev[i] = a;
        }
        p->amp_sum += mean;
        p->prev_n = n;
    }
    portEXIT_CRITICAL_ISR(&s_lock);
}

#ifdef WIFI_SSID
static void on_wifi(void *arg, esp_event_base_t base, int32_t id, void *data) {
    (void)arg; (void)data;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        s_got_ip = false;
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        s_got_ip = true;
        uint8_t ch; wifi_second_chan_t sc;
        esp_wifi_get_channel(&ch, &sc);
        ESP_LOGI(TAG, "got IP, router channel %d", ch);
    }
}
#endif

static void tx_task(void *arg) {
    (void)arg;
    uint32_t seq = 0;
    // Offset the nodes inside the period to avoid constant collisions.
    vTaskDelay(pdMS_TO_TICKS((s_self[5] % 4) * (TX_PERIOD_MS / 4)));
    TickType_t last = xTaskGetTickCount();
    while (1) {
        mesh_frame_t f = {.magic = MAGIC, .seq = seq++, .tx_ms = (uint32_t)(esp_timer_get_time() / 1000)};
        esp_now_send(BCAST, (const uint8_t *)&f, sizeof(f));
        vTaskDelayUntil(&last, pdMS_TO_TICKS(TX_PERIOD_MS));
    }
}

static void report_task(void *arg) {
    (void)arg;
    TickType_t last = xTaskGetTickCount();
    while (1) {
        vTaskDelayUntil(&last, pdMS_TO_TICKS(REPORT_PERIOD_MS));
        uint32_t now_ms = (uint32_t)(esp_timer_get_time() / 1000);
        char out[1024];
        int off = 0;
        for (int i = 0; i < MAX_PEERS; i++) {
            peer_t snap;
            portENTER_CRITICAL(&s_lock);
            if (!s_peers[i].used) { portEXIT_CRITICAL(&s_lock); continue; }
            snap = s_peers[i];
            s_peers[i].esp_now_rx = 0;
            s_peers[i].csi_n = 0;
            s_peers[i].rssi_sum = 0;
            s_peers[i].turb_mean = 0;
            s_peers[i].turb_m2 = 0;
            s_peers[i].diff_sum = 0;
            s_peers[i].diff_n = 0;
            s_peers[i].amp_sum = 0;
            memset(s_peers[i].sub_mean, 0, sizeof(s_peers[i].sub_mean));
            memset(s_peers[i].sub_m2, 0, sizeof(s_peers[i].sub_m2));
            s_peers[i].lost = 0;
            portEXIT_CRITICAL(&s_lock);
            double var = snap.csi_n > 1 ? snap.turb_m2 / (snap.csi_n - 1) : 0;
            double diff = snap.diff_n ? snap.diff_sum / snap.diff_n : 0;
            double rssi = snap.csi_n ? snap.rssi_sum / snap.csi_n : 0;
            double tvar = 0;
            if (snap.csi_n > 1 && snap.prev_n) {
                for (int k = 0; k < snap.prev_n; k++) tvar += snap.sub_m2[k] / (snap.csi_n - 1);
                tvar /= snap.prev_n;
            }
            double amp = snap.csi_n ? snap.amp_sum / snap.csi_n : 0;
            off += snprintf(out + off, sizeof(out) - off, "L,%02x%02x%02x%02x%02x%02x,%02x%02x%02x%02x%02x%02x,%lu,%lu,%lu,%lu,%lu,%.1f,%.6f,%.6f,%.6f,%.6f,%.3f\n",
                   s_self[0], s_self[1], s_self[2], s_self[3], s_self[4], s_self[5],
                   snap.mac[0], snap.mac[1], snap.mac[2], snap.mac[3], snap.mac[4], snap.mac[5],
                   (unsigned long)now_ms, (unsigned long)snap.esp_now_rx, (unsigned long)snap.csi_n,
                   (unsigned long)snap.csi_len, (unsigned long)snap.lost, rssi, snap.turb_mean, var, diff, tvar, amp);
        }
        off += snprintf(out + off, sizeof(out) - off, "T,%lu,%lu\n", (unsigned long)s_tx_ok, (unsigned long)s_tx_fail);
        fputs(out, stdout);
        if (s_sock >= 0 && s_got_ip) {
            int r = sendto(s_sock, out, off, 0, (struct sockaddr *)&s_dest, sizeof(s_dest));
            if (r < 0) ESP_LOGW(TAG, "udp send failed errno=%d", errno);
        }
        s_tx_ok = s_tx_fail = 0;
    }
}

void app_main(void) {
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
#ifdef WIFI_SSID
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_wifi, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_wifi, NULL));
    wifi_config_t wc = {0};
    strncpy((char *)wc.sta.ssid, WIFI_SSID, sizeof(wc.sta.ssid) - 1);
    strncpy((char *)wc.sta.password, WIFI_PASSWORD, sizeof(wc.sta.password) - 1);
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wc));
    s_sock = socket(AF_INET, SOCK_DGRAM, 0);
    s_dest.sin_family = AF_INET;
    s_dest.sin_port = htons(UDP_PORT);
    inet_pton(AF_INET, PC_IP, &s_dest.sin_addr);
#endif
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    ESP_ERROR_CHECK(esp_wifi_set_bandwidth(WIFI_IF_STA, WIFI_BW20));
#ifndef WIFI_SSID
#ifndef WIFI_SSID
    ESP_ERROR_CHECK(esp_wifi_set_channel(MESH_CHANNEL, WIFI_SECOND_CHAN_NONE));
#endif
#endif
    esp_read_mac(s_self, ESP_MAC_WIFI_STA);

    ESP_ERROR_CHECK(esp_now_init());
    ESP_ERROR_CHECK(esp_now_register_recv_cb(on_recv));
    ESP_ERROR_CHECK(esp_now_register_send_cb(on_sent));
    esp_now_peer_info_t peer = {.channel = 0, .ifidx = WIFI_IF_STA, .encrypt = false};
    memcpy(peer.peer_addr, BCAST, 6);
    ESP_ERROR_CHECK(esp_now_add_peer(&peer));
    esp_now_rate_config_t rate = {.phymode = WIFI_PHY_MODE_HT20, .rate = WIFI_PHY_RATE_MCS0_LGI, .ersu = false, .dcm = false};
    err = esp_now_set_peer_rate_config(BCAST, &rate);
    ESP_LOGI(TAG, "set_peer_rate_config: %s", esp_err_to_name(err));

    wifi_csi_config_t csi_cfg = {
        .lltf_en = false,
        .htltf_en = true,
        .stbc_htltf2_en = false,
        .ltf_merge_en = false,
        .channel_filter_en = false,
        .manu_scale = false,
        .shift = 0,
        .dump_ack_en = false,
    };
    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&csi_cfg));
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(on_csi, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));

    ESP_LOGI(TAG, "node %02x%02x%02x%02x%02x%02x ch%d tx every %d ms", s_self[0], s_self[1], s_self[2],
             s_self[3], s_self[4], s_self[5], MESH_CHANNEL, TX_PERIOD_MS);
    xTaskCreate(tx_task, "tx", 3072, NULL, 5, NULL);
    xTaskCreate(report_task, "report", 6144, NULL, 4, NULL);
}
