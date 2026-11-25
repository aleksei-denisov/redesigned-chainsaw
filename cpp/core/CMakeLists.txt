# Qt-free core: physics, control, simulation, session (mirrors pendulum_sim/*).
add_library(pendulum_core
    src/version.cpp
    src/physics/params.cpp
    src/physics/model.cpp
    src/physics/actuator.cpp
    src/control/pid.cpp
    src/control/multiloop.cpp
    src/control/riccati.cpp
    src/control/lqr.cpp
    src/io/config_json.cpp
    src/simulation/config.cpp
    src/simulation/telemetry.cpp
    src/simulation/engine.cpp
    src/simulation/realtime.cpp
    src/simulation/metrics.cpp
    src/simulation/experiment.cpp
    src/simulation/scenarios.cpp
    src/simulation/tuning.cpp
)
target_include_directories(pendulum_core PUBLIC include)
find_package(Threads REQUIRED)
target_link_libraries(pendulum_core PUBLIC Eigen3::Eigen nlohmann_json::nlohmann_json Threads::Threads)
if(MSVC)
    target_compile_options(pendulum_core PRIVATE /W4 /permissive-)
else()
    target_compile_options(pendulum_core PRIVATE -Wall -Wextra -Wpedantic $<$<CXX_COMPILER_ID:GNU>:-Wno-maybe-uninitialized>)
endif()
