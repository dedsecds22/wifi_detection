idf_component_register(SRCS "mesh_main.c"
                       INCLUDE_DIRS ".")
if(DEFINED ENV{MESH_WIFI_SSID})
    target_compile_definitions(${COMPONENT_LIB} PRIVATE WIFI_SSID="$ENV{MESH_WIFI_SSID}" WIFI_PASSWORD="$ENV{MESH_WIFI_PASSWORD}" PC_IP="$ENV{MESH_PC_IP}")
endif()
