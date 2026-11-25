# Qt Quick app on the C++ core (Phase 5). The QML is shared with the Python Qt Quick app:
# pendulum_sim/quick/qml is compiled into the app's resources unchanged.
find_package(Qt6 6.4 REQUIRED COMPONENTS Quick Gui)
set(CMAKE_AUTOMOC ON)

set(QUICK_DIR ${CMAKE_CURRENT_SOURCE_DIR}/../../pendulum_sim/quick)
file(GLOB QML_FILES ${QUICK_DIR}/qml/*.qml)
file(GLOB FONT_FILES ${QUICK_DIR}/fonts/*.ttf)

qt_add_executable(pendulum_app
    main.cpp
    backend/simhost.cpp backend/simhost.h
    backend/simulation.cpp backend/simulation.h
    backend/settings.cpp backend/settings.h
    backend/graphmath.cpp backend/graphmath.h
    backend/graphs.cpp backend/graphs.h
    backend/lineitem.cpp backend/lineitem.h
    backend/panel.cpp backend/panel.h
    backend/frames.cpp backend/frames.h
    backend/benchmark.cpp backend/benchmark.h
    backend/placeholders.h
)
qt_add_resources(pendulum_app "shared_qml"
    PREFIX "/"
    BASE ${QUICK_DIR}
    FILES ${QML_FILES} ${FONT_FILES})
target_link_libraries(pendulum_app PRIVATE pendulum_core Qt6::Quick Qt6::Gui)
target_compile_definitions(pendulum_app PRIVATE QT_NO_KEYWORDS)
set_target_properties(pendulum_app PROPERTIES
    MACOSX_BUNDLE TRUE
    WIN32_EXECUTABLE TRUE
    OUTPUT_NAME "PendulumCpp"
    # Android: own application id, so it installs next to both Python apps
    QT_ANDROID_PACKAGE_NAME "com.adenisov.invertedpendulumcpp"
    QT_ANDROID_APP_NAME "Pendulum C++"
    QT_ANDROID_VERSION_NAME "0.5"
    QT_ANDROID_VERSION_CODE 5
    QT_ANDROID_MIN_SDK_VERSION 28
    QT_ANDROID_TARGET_SDK_VERSION 35
    # androiddeployqt scans these QML files for the Qt Quick modules to bundle
    QT_QML_ROOT_PATH ${QUICK_DIR}/qml)

# Android launcher name: Qt 6.8 names the app after the target, so use Qt's own manifest
# template with the label filled in (other placeholders stay for androiddeployqt).
if(ANDROID)
    set(_template "${QT6_INSTALL_PREFIX}/src/android/templates/AndroidManifest.xml")
    if(EXISTS "${_template}")
        file(READ "${_template}" _manifest)
        string(REPLACE "-- %%INSERT_APP_NAME%% --" "Pendulum C++" _manifest "${_manifest}")
        file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/android/AndroidManifest.xml" "${_manifest}")
        set_property(TARGET pendulum_app PROPERTY QT_ANDROID_PACKAGE_SOURCE_DIR "${CMAKE_CURRENT_BINARY_DIR}/android")
    else()
        message(WARNING "Qt Android manifest template not found: ${_template} – the launcher shows the target name")
    endif()
endif()
