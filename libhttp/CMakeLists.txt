cmake_minimum_required(VERSION 3.12)
project(us VERSION 0.0.1)

# Set C++ standard
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Set compiler flags based on compiler type
if(MSVC)
    set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} /O2 /W4 /bigobj")
else()
    set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -O3 -pipe -Wall")
endif()

# Library names (与 Makefile 保持一致)
set(SERVER_LIB_NAME "us_http")
set(CLIENT_LIB_NAME "us_httpclient")

# ===== Boost Library Configuration (Header-Only) =====
if(DEFINED ENV{BOOST_ROOT})
    set(BOOST_ROOT $ENV{BOOST_ROOT})
    message(STATUS "Using BOOST_ROOT from environment: ${BOOST_ROOT}")
elseif(NOT DEFINED BOOST_ROOT)
    set(BOOST_ROOT "C:/local/boost_1_91_0" CACHE PATH "Boost installation root")
endif()

message(STATUS "BOOST_ROOT: ${BOOST_ROOT}")

set(Boost_INCLUDE_DIR "${BOOST_ROOT}" CACHE PATH "Boost include directory")
message(STATUS "Boost include directory: ${Boost_INCLUDE_DIR}")

if(NOT EXISTS ${Boost_INCLUDE_DIR})
    message(FATAL_ERROR "Boost include directory not found: ${Boost_INCLUDE_DIR}")
endif()

include_directories(${Boost_INCLUDE_DIR})

# Include directory (project headers)
set(INCLUDE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/inc")
include_directories(${INCLUDE_DIR})

# Lowest-path OpenSSL/libcurl (macOS homebrew) — 可被环境覆盖
set(EXTERNAL_LIB_DIR "/opt/homebrew/lib")
set(EXTERNAL_INC_DIR "/opt/homebrew/include")
include_directories(${EXTERNAL_INC_DIR})

# Server sources (HttpServer 拆分后的 6 个 TU)
set(SERVER_SOURCES
    src/HttpServer.cpp
    src/HttpServer_Listener.cpp
    src/HttpServer_Routes.cpp
    src/HttpServer_Request.cpp
    src/HttpServer_Ws.cpp
    src/HttpServer_WsSession.cpp
)

# Client sources
set(CLIENT_SOURCES
    src/HttpClient.cpp
)

# Server library: libus_http.so (HTTP + WebSocket server + WS proxy, 不依赖 curl)
add_library(${SERVER_LIB_NAME} SHARED ${SERVER_SOURCES})
target_link_libraries(${SERVER_LIB_NAME}
    ${EXTERNAL_LIB_DIR}/libssl.a
    ${EXTERNAL_LIB_DIR}/libcrypto.a
    pthread
)

# Client library: libus_httpclient.so (HttpClient, 依赖 curl)
add_library(${CLIENT_LIB_NAME} SHARED ${CLIENT_SOURCES})
target_link_libraries(${CLIENT_LIB_NAME}
    ${EXTERNAL_LIB_DIR}/libssl.a
    ${EXTERNAL_LIB_DIR}/libcrypto.a
    ${EXTERNAL_LIB_DIR}/libcurl.a
    pthread
)

# Set output directories
set_target_properties(${SERVER_LIB_NAME} ${CLIENT_LIB_NAME} PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/build"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/build"
    ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/build"
)

# Install targets
install(TARGETS ${SERVER_LIB_NAME} ${CLIENT_LIB_NAME}
    LIBRARY DESTINATION lib
    RUNTIME DESTINATION bin
    ARCHIVE DESTINATION lib
)

# Install headers
install(FILES
    inc/us/HttpServer.hpp
    inc/us/HttpClient.hpp
    DESTINATION include/us
)
install(FILES
    inc/us/detail/HttpServerImpl.hpp
    DESTINATION include/us/detail
)

# Clean target
add_custom_target(clear
    COMMAND ${CMAKE_COMMAND} -E remove_directory "${CMAKE_CURRENT_BINARY_DIR}/build"
    COMMENT "Cleaning build directory..."
)

# Print configuration info
message(STATUS "========================================")
message(STATUS "Project: ${PROJECT_NAME}")
message(STATUS "Version: ${PROJECT_VERSION}")
message(STATUS "Server library: ${SERVER_LIB_NAME}")
message(STATUS "Client library: ${CLIENT_LIB_NAME}")
message(STATUS "Build type: ${CMAKE_BUILD_TYPE}")
message(STATUS "Install prefix: ${CMAKE_INSTALL_PREFIX}")
message(STATUS "Compiler: ${CMAKE_CXX_COMPILER_ID}")
message(STATUS "========================================")