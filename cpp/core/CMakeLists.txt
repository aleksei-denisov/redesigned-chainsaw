# Qt-free core: physics, control, simulation, session (mirrors pendulum_sim/*).
add_library(pendulum_core
    src/version.cpp
)
target_include_directories(pendulum_core PUBLIC include)
target_link_libraries(pendulum_core PUBLIC Eigen3::Eigen)
if(MSVC)
    target_compile_options(pendulum_core PRIVATE /W4)
else()
    target_compile_options(pendulum_core PRIVATE -Wall -Wextra -Wpedantic)
endif()
