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
)
target_include_directories(pendulum_core PUBLIC include)
target_link_libraries(pendulum_core PUBLIC Eigen3::Eigen nlohmann_json::nlohmann_json)
if(MSVC)
    target_compile_options(pendulum_core PRIVATE /W4 /permissive-)
else()
    target_compile_options(pendulum_core PRIVATE -Wall -Wextra -Wpedantic)
endif()
