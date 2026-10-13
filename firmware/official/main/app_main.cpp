// SPDX-License-Identifier: GPL-3.0-only
// Commercial licensing available under separate agreement; see LICENSING.md.
#include "espectre_services_sdk.h"

#include <atomic>

#include <esp_log.h>
#include <esp_mac.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

namespace {

constexpr const char *TAG = "espectre.example";

// The sink forwards levels by value: LogLevel follows ESP-IDF's numbering.
static_assert(static_cast<int>(espectre::LogLevel::ERROR) == ESP_LOG_ERROR);
static_assert(static_cast<int>(espectre::LogLevel::WARNING) == ESP_LOG_WARN);
static_assert(static_cast<int>(espectre::LogLevel::INFO) == ESP_LOG_INFO);
static_assert(static_cast<int>(espectre::LogLevel::DEBUG) == ESP_LOG_DEBUG);
static_assert(static_cast<int>(espectre::LogLevel::VERBOSE) == ESP_LOG_VERBOSE);

bool log_enabled(void *, espectre::LogLevel level, const char *tag) {
  return static_cast<esp_log_level_t>(level) <= esp_log_level_get(tag);
}

void log_write(void *, espectre::LogLevel level, const char *tag, int,
               const char *format, va_list args) {
  esp_log_va(ESP_LOG_CONFIG_INIT(static_cast<esp_log_level_t>(level) | ESP_LOG_CONFIGS_DEFAULT),
             tag, format, args);
}

espectre::RuntimeDirectHttpBridge direct_bridge;
std::atomic<bool> wifi_connected{false};

class MotionListener : public espectre::IRuntimeListener {
 public:
  void on_motion_state_changed(const espectre::RuntimeSnapshot &snapshot) override {
    if (snapshot.ready_to_publish) {
      ESP_LOGI(TAG, "Motion: %s", snapshot.motion_state == espectre::MotionState::MOTION ? "detected" : "idle");
    }
  }

  void on_sensing_readiness_changed(const espectre::RuntimeSnapshot &snapshot) override {
    ESP_LOGI(TAG, "Sensing: %s", snapshot.ready_to_publish ? "ready" : "waiting for Wi-Fi or calibration");
    (void) direct_bridge.publish_changes(espectre::FrontendCommandChange::SENSING);
  }

  void on_live_telemetry(const espectre::RuntimeSnapshot &snapshot) override {
    if (snapshot.ready_to_publish) {
      (void) direct_bridge.publish_motion(snapshot);
    }
  }

  void on_runtime_fault(const char *message) override { ESP_LOGE(TAG, "Runtime: %s", message); }
};

MotionListener listener;
espectre::StandaloneWifiService wifi;
espectre::RuntimeFrontendController runtime;
espectre::EspIdfDirectHttpService direct_service;

uint64_t read_device_id() {
  uint8_t mac[6] = {};
  ESP_ERROR_CHECK(esp_read_mac(mac, ESP_MAC_WIFI_STA));
  uint64_t id = 0U;
  for (uint8_t byte : mac) {
    id = (id << 8U) | byte;
  }
  return id;
}

void start_direct_http() {
  espectre::RuntimeDirectHttpBridgeConfig config;
  config.frontend = "example";
  config.device_name = "espectre-node";
  config.firmware_version = espectre::espectre_sdk_version();
  config.chip = CONFIG_IDF_TARGET;
  config.device_id = read_device_id();
  config.allow_missing_origin = true;  // Local PC collector, not a browser.
  config.diagnostics_sample_getter = []() { return runtime.diagnostics_sample(); };
  if (direct_bridge.setup(&direct_service, &runtime, config)) {
    ESP_LOGI(TAG, "Direct HTTP ready on port %u, device_id %016llx", static_cast<unsigned>(config.port),
             static_cast<unsigned long long>(config.device_id));
  } else {
    ESP_LOGE(TAG, "Unable to start Direct HTTP");
  }
}

}  // namespace

extern "C" void app_main() {
  if (!espectre::set_log_sink({nullptr, log_enabled, log_write})) {
    ESP_LOGE(TAG, "Unable to register SDK logging");
    return;
  }
  ESP_LOGI(TAG, "ESPectre SDK %s", espectre::espectre_sdk_version());
  ESP_ERROR_CHECK(espectre::nvs_init_with_erase_fallback());
  if (CONFIG_ESPECTRE_EXAMPLE_WIFI_SSID[0] == '\0') {
    ESP_LOGE(TAG, "Set the Wi-Fi credentials under ESPectre example in menuconfig");
    return;
  }

  const auto sensing = espectre::make_runtime_sensing_config_from_kconfig();
  espectre::StandaloneWifiConfig config;
  config.ssid = CONFIG_ESPECTRE_EXAMPLE_WIFI_SSID;
  config.password = CONFIG_ESPECTRE_EXAMPLE_WIFI_PASSWORD;
  config.bssid = CONFIG_ESPECTRE_EXAMPLE_WIFI_BSSID;
  config.band_policy = sensing.wifi_band_policy;
  config.manage_csi_lifecycle = false;  // The sensing runtime owns CSI.
  ESP_ERROR_CHECK(wifi.setup(config,
                            []() {
                              ESP_LOGI(TAG, "Wi-Fi connected");
                              wifi_connected = true;
                            },
                            []() { ESP_LOGW(TAG, "Wi-Fi disconnected"); }));
  runtime.set_config(sensing);
  runtime.set_live_telemetry_enabled(true);
  if (!runtime.setup(&listener)) {
    ESP_LOGE(TAG, "Unable to initialize sensing");
    return;
  }
  const esp_err_t error = wifi.start();
  if (error != ESP_OK) {
    runtime.shutdown();
    ESP_LOGE(TAG, "Unable to start Wi-Fi: %s", esp_err_to_name(error));
    return;
  }

  // Service Wi-Fi before sensing, keeping all SDK lifecycle calls on this task.
  while (true) {
    wifi.loop();
    runtime.loop();
    if (wifi_connected && !direct_bridge.running()) {
      start_direct_http();
    }
    direct_bridge.loop();
    vTaskDelay(pdMS_TO_TICKS(10));
  }
}
