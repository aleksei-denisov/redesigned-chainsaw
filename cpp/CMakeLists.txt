# C++ port of the Inverted Pendulum Simulator (branch cpp-core).
# Plan: docs/CPP_PORT.md. The Python package stays the reference implementation.
cmake_minimum_required(VERSION 3.21)
project(PendulumSim VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(PENDULUM_BUILD_TESTS "Build the core unit and reference tests" ON)
option(PENDULUM_BUILD_APP   "Build the Qt Quick application (needs Qt 6)" OFF)

if(POLICY CMP0169)
    cmake_policy(SET CMP0169 OLD)  # allow FetchContent_Populate for the header-only Eigen
endif()
include(FetchContent)
# Eigen: header-only linear algebra (replaces NumPy/SciPy in the core).
# A system Eigen 3.4 is used when present; otherwise it is fetched from the GitHub mirror
# (the official GitLab repository is not reachable from every build environment).
find_package(Eigen3 3.4 QUIET NO_MODULE)
if(NOT TARGET Eigen3::Eigen)
    FetchContent_Declare(eigen
        GIT_REPOSITORY https://github.com/eigen-mirror/eigen.git
        GIT_TAG 3.4.0
        GIT_SHALLOW TRUE)
    FetchContent_GetProperties(eigen)
    if(NOT eigen_POPULATED)
        FetchContent_Populate(eigen)
    endif()
    add_library(Eigen3::Eigen INTERFACE IMPORTED GLOBAL)
    target_include_directories(Eigen3::Eigen SYSTEM INTERFACE ${eigen_SOURCE_DIR})
endif()

# nlohmann/json: config and result files (Phase 4) and the golden reference data in the tests.
FetchContent_Declare(nlohmann_json
    URL https://github.com/nlohmann/json/releases/download/v3.11.3/json.tar.xz
    URL_HASH SHA256=d6c65aca6b1ed68e7a182f4757257b107ae403032760ed6ef121c9d55e81757d
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
FetchContent_MakeAvailable(nlohmann_json)

add_subdirectory(core)

if(PENDULUM_BUILD_TESTS)
    enable_testing()
    add_subdirectory(tests)
endif()

if(PENDULUM_BUILD_APP)
    add_subdirectory(app)
endif()

option(PENDULUM_BUILD_BENCH "Build the core benchmark" ON)
if(PENDULUM_BUILD_BENCH)
    add_executable(pendulum_bench bench/bench_core.cpp)
    target_link_libraries(pendulum_bench PRIVATE pendulum_core)
endif()
