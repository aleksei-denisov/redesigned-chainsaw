// Pendulum C++ – the Qt Quick app on the C++ core (Phase 5, docs/CPP_PORT.md).
// The QML is shared with the Python Qt Quick app (pendulum_sim/quick/qml); the backend objects
// below give it the same names and properties as the Python bridges.
#include <QCommandLineParser>
#include <QFont>
#include <QFontDatabase>
#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QQuickWindow>
#include <QScreen>
#include <QTimer>
#include <cstdio>
#include <memory>

#include "backend/benchmark.h"
#include "backend/frames.h"
#include "backend/graphs.h"
#include "backend/lineitem.h"
#include "backend/panel.h"
#include "backend/placeholders.h"
#include "backend/settings.h"
#include "backend/simulation.h"
#include "pendulum/simulation/scenarios.hpp"

using namespace app;

namespace {

struct Backend {
    SimulationBridge* sim = nullptr;
    GraphsBridge* graphs = nullptr;
    PanelBridge* panel = nullptr;
    TrailBridge* trail = nullptr;
    ScenarioBridge* scenarios = nullptr;
    FrameDriver* frames = nullptr;
    Benchmark* bench = nullptr;
};

}  // namespace

int main(int argc, char* argv[]) {
    QGuiApplication app(argc, argv);
    QGuiApplication::setOrganizationName("adenisov");
    QGuiApplication::setApplicationName("PendulumCpp");
    QGuiApplication::setApplicationDisplayName("Pendulum C++");

    QCommandLineParser cli;
    cli.addHelpOption();
    QCommandLineOption themeOpt("theme", "Theme for this run (system, light, dark).", "theme");
    QCommandLineOption screenOpt("screen", "First screen.", "screen", "simulate");
    QCommandLineOption scenarioOpt("scenario", "Scenario to start with.", "key", pendulum::kDefaultScenario);
    QCommandLineOption benchOpt("benchmark", "Run the performance test, print the report and exit.");
    QCommandLineOption smokeOpt("smoke-test", "Visit the ported screens for N seconds each, then exit.", "seconds");
    QCommandLineOption shotOpt("screenshot", "Save a screenshot of the window after the start, then exit.", "file");
    QCommandLineOption sizeOpt("size", "Window size WxH.", "size");
    cli.addOptions({themeOpt, screenOpt, scenarioOpt, benchOpt, smokeOpt, shotOpt, sizeOpt});
    cli.process(app);

    for (const char* f : {"SpaceGrotesk-Regular.ttf", "SpaceGrotesk-Medium.ttf", "SpaceGrotesk-Bold.ttf",
                          "JetBrainsMono-Regular.ttf", "JetBrainsMono-Medium.ttf"})
        if (QFontDatabase::addApplicationFont(QString(":/fonts/") + f) < 0) std::fprintf(stderr, "font not loaded: %s\n", f);
    app.setFont(QFont("Space Grotesk"));

    auto* settings = new SettingsBridge(&app);
    if (cli.isSet(themeOpt)) settings->overrideTheme(cli.value(themeOpt));
    auto* shell = new AppBridge(&app);
    shell->setScreen(cli.value(screenOpt));
    qmlRegisterSingletonInstance("PendulumApp", 1, 0, "Settings", settings);
    qmlRegisterSingletonInstance("PendulumApp", 1, 0, "App", shell);
    qmlRegisterSingletonType(QUrl("qrc:/qml/Theme.qml"), "Instrument", 1, 0, "Theme");
    qmlRegisterType<GraphLines>("PendulumItems", 1, 0, "GraphLines");

    QQmlApplicationEngine engine;
    int warnings = 0;
    QObject::connect(&engine, &QQmlApplicationEngine::warnings, [&](const QList<QQmlError>& list) {
        for (const auto& w : list) {
            ++warnings;
            std::fprintf(stderr, "QML: %s\n", qPrintable(w.toString()));
        }
    });
    engine.load(QUrl("qrc:/qml/Main.qml"));
    if (engine.rootObjects().isEmpty()) {
        std::fprintf(stderr, "Main.qml could not be loaded\n");
        return 1;
    }
    auto* window = qobject_cast<QQuickWindow*>(engine.rootObjects().first());
    if (cli.isSet(sizeOpt)) {
        const auto wh = cli.value(sizeOpt).split('x');
        if (wh.size() == 2) window->resize(wh[0].toInt(), wh[1].toInt());
    }
#ifdef Q_OS_ANDROID
    window->showMaximized();
#endif
    Backend b;
    int result = 0;

    QObject::connect(shell, &AppBridge::exitRequested, window, [window] { window->setProperty("askExit", true); });
    QObject::connect(shell, &AppBridge::failed, &app, [&] {
        result = 1;
        if (cli.isSet(smokeOpt) || cli.isSet(benchOpt) || cli.isSet(shotOpt)) app.exit(1);
    });

    // The loading screen is the first frame; the backend is created after it.
    QObject::connect(shell, &AppBridge::loadingShown, &app, [&] {
        shell->setStatus("Loading the simulation core…", 0.25);
        QTimer::singleShot(0, &app, [&] {
            pendulum::AppConfig cfg;
            try {
                cfg = pendulum::scenario(cli.value(scenarioOpt).toStdString());
            } catch (const std::exception&) {
                cfg = pendulum::scenario(pendulum::kDefaultScenario);
            }
            b.sim = new SimulationBridge(cfg, &app);
            b.graphs = new GraphsBridge(b.sim, &app);
            b.graphs->setRateHz(settings->graphRate());
            b.panel = new PanelBridge(b.sim, b.graphs, &app);
            b.trail = new TrailBridge(b.sim, &app);
            b.scenarios = new ScenarioBridge(b.sim, &app);
            const QScreen* screen = window->screen();
            b.frames = new FrameDriver(b.sim, screen ? screen->refreshRate() : 60.0, settings->frameRate(), &app);
            b.graphs->driveFromFrames();
            b.frames->listeners.push_back([g = b.graphs](double now) { g->onFrame(now); });
            b.frames->listeners.push_back([p = b.panel](double now) { p->onFrame(now); });
            b.frames->listeners.push_back([t = b.trail](double now) { t->onFrame(now); });
            QObject::connect(window, &QQuickWindow::frameSwapped, b.frames, &FrameDriver::swapped, Qt::QueuedConnection);
            b.bench = new Benchmark(shell, b.sim, b.graphs, b.frames, &app);
            b.bench->window = window;
            auto* placeholder = new Placeholder(&app);
            QQmlContext* ctx = engine.rootContext();
            ctx->setContextProperty("sim", b.sim);
            ctx->setContextProperty("graphs", b.graphs);
            ctx->setContextProperty("panel", b.panel);
            ctx->setContextProperty("trail", b.trail);
            ctx->setContextProperty("scenarios", b.scenarios);
            ctx->setContextProperty("frames", b.frames);
            ctx->setContextProperty("bench", b.bench);
            for (const char* name : {"model", "controller", "experiments", "autotune"})
                ctx->setContextProperty(name, placeholder);
            QObject::connect(settings, &SettingsBridge::frameRateChanged, b.frames,
                             [&] { b.frames->setMode(settings->frameRate()); });
            QObject::connect(settings, &SettingsBridge::graphRateChanged, b.graphs,
                             [&] { b.graphs->setRateHz(settings->graphRate()); });
            if (screen) QObject::connect(screen, &QScreen::refreshRateChanged, b.frames, &FrameDriver::setRefresh);
            QObject::connect(&app, &QGuiApplication::applicationStateChanged, b.sim, [&](Qt::ApplicationState s) {
                if (s == Qt::ApplicationActive)
                    b.sim->resume();
                else if (s == Qt::ApplicationSuspended || s == Qt::ApplicationHidden)
                    b.sim->suspend();
            });
            shell->setStatus("Building the Simulate screen…", 0.6);
            shell->setReady();
            b.sim->setRunning(true);
        });
    });

    QObject::connect(shell, &AppBridge::shellShown, &app, [&] {
        if (cli.isSet(benchOpt)) {
            QObject::connect(b.bench, &Benchmark::finished, &app, [&] {
                std::printf("%s\n", qPrintable(b.bench->report()));
                std::fflush(stdout);
                app.exit(result);
            });
            QTimer::singleShot(1500, b.bench, &Benchmark::start);
        } else if (cli.isSet(smokeOpt)) {
            const int ms = static_cast<int>(1000 * cli.value(smokeOpt).toDouble());
            const QStringList screens = {"simulate", "graphs", "settings", "simulate"};
            for (int i = 0; i < screens.size(); ++i)
                QTimer::singleShot(ms * i, &app, [&, s = screens[i]] {
                    shell->setScreen(s);
                    std::printf("screen %s · %s · %s\n", qPrintable(s), qPrintable(b.sim->fpsText()),
                                qPrintable(b.frames->statsText().replace('\n', " · ")));
                });
            QTimer::singleShot(ms * screens.size(), &app, [&] {
                std::printf("smoke test done, %d QML warnings\n", warnings);
                app.exit(warnings ? 2 : result);
            });
        } else if (cli.isSet(shotOpt)) {
            QTimer::singleShot(2500, &app, [&] {
                const QImage img = window->grabWindow();
                const bool ok = img.save(cli.value(shotOpt));
                std::printf("screenshot %s: %s\n", ok ? "saved" : "FAILED", qPrintable(cli.value(shotOpt)));
                app.exit(ok ? result : 1);
            });
        }
    });

    // the graph worker reads the physics host: stop it before the backend objects go away
    QObject::connect(&app, &QCoreApplication::aboutToQuit, [&] {
        if (b.graphs) b.graphs->shutdown();
    });
    const int code = app.exec();
    return code ? code : result;
}
