# Qt Quick application (Phase 5). Reuses the QML from pendulum_sim/quick/qml.
find_package(Qt6 6.5 REQUIRED COMPONENTS Quick)
qt_standard_project_setup(REQUIRES 6.5)

qt_add_executable(pendulum_app main.cpp)
qt_add_qml_module(pendulum_app
    URI PendulumApp
    VERSION 1.0
    QML_FILES qml/Main.qml)
target_link_libraries(pendulum_app PRIVATE pendulum_core Qt6::Quick)
set_target_properties(pendulum_app PROPERTIES
    MACOSX_BUNDLE TRUE
    WIN32_EXECUTABLE TRUE
    QT_ANDROID_PACKAGE_NAME "io.github.alekseidenisov.pendulumsim.cpp")
