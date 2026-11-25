#include "settings.h"

#include <QClipboard>
#include <QGuiApplication>
#include <QStyleHints>

#include "pendulum/simulation/scenarios.hpp"
#include "simulation.h"

namespace app {

namespace {
QString readChoice(QSettings& s, const QString& key, const QString& def, const QStringList& allowed) {
    const QString v = s.value("quick/" + key, def).toString();
    return allowed.contains(v) ? v : def;
}
const QStringList kScreens = {"simulate", "graphs", "controller", "model", "autotune", "experiments", "settings"};
}  // namespace

// ----------------------------------------------------------------- Settings
SettingsBridge::SettingsBridge(QObject* parent) : QObject(parent) {
    theme_ = readChoice(store_, "theme", "system", {"system", "light", "dark"});
    frame_rate_ = readChoice(store_, "frame_rate", "auto", {"auto", "120", "60", "40", "30"});
    graph_rate_ = readChoice(store_, "graph_rate", "25", {"20", "25", "30"}).toInt();
    const QVariant ov = store_.value("quick/overlay", false);
    overlay_ = ov.toBool() || ov.toString() == "true" || ov.toString() == "1";
    system_dark_ = systemDark();
#if QT_VERSION >= QT_VERSION_CHECK(6, 5, 0)
    connect(QGuiApplication::styleHints(), &QStyleHints::colorSchemeChanged, this, [this] {
        const bool dark = systemDark();
        if (dark != system_dark_) {
            system_dark_ = dark;
            if (theme_ == "system") Q_EMIT darkChanged();
        }
    });
#endif
}

bool SettingsBridge::systemDark() const {
#if QT_VERSION >= QT_VERSION_CHECK(6, 5, 0)
    return QGuiApplication::styleHints()->colorScheme() == Qt::ColorScheme::Dark;
#else
    return false;
#endif
}

bool SettingsBridge::isDark() const { return theme_ == "dark" || (theme_ == "system" && system_dark_); }

void SettingsBridge::setTheme(const QString& v) {
    if (!themes().contains(v) || v == theme_) return;
    const bool before = isDark();
    theme_ = v;
    store_.setValue("quick/theme", v);
    Q_EMIT themeChanged();
    if (isDark() != before) Q_EMIT darkChanged();
}

void SettingsBridge::overrideTheme(const QString& v) {
    if (!themes().contains(v) || v == theme_) return;
    const bool before = isDark();
    theme_ = v;
    Q_EMIT themeChanged();
    if (isDark() != before) Q_EMIT darkChanged();
}

void SettingsBridge::setFrameRate(const QString& v) {
    if (frameRates().contains(v) && v != frame_rate_) {
        frame_rate_ = v;
        store_.setValue("quick/frame_rate", v);
        Q_EMIT frameRateChanged();
    }
}

void SettingsBridge::setGraphRate(int v) {
    if (graphRates().contains(v) && v != graph_rate_) {
        graph_rate_ = v;
        store_.setValue("quick/graph_rate", QString::number(v));
        Q_EMIT graphRateChanged();
    }
}

void SettingsBridge::setOverlay(bool on) {
    if (on != overlay_) {
        overlay_ = on;
        store_.setValue("quick/overlay", on);
        Q_EMIT overlayChanged();
    }
}

// ---------------------------------------------------------------------- App
void AppBridge::setStatus(const QString& text, double progress) {
    status_ = text;
    progress_ = progress;
    Q_EMIT statusChanged();
}

void AppBridge::setReady() {
    if (!ready_) {
        ready_ = true;
        Q_EMIT readyChanged();
    }
}

void AppBridge::setScreen(const QString& key) {
    if (kScreens.contains(key) && key != screen_) {
        screen_ = key;
        Q_EMIT screenChanged();
    }
}

QVariantList AppBridge::screens() const {
    QVariantList l;
    for (const auto& s : kScreens) l << s;
    return l;
}

void AppBridge::copyPreviousReport() {
    QGuiApplication::clipboard()->setText(previous_);
    copied_ = true;
    Q_EMIT previousChanged();
}

void AppBridge::continueStart() {
    previous_.clear();
    Q_EMIT previousChanged();
    Q_EMIT continueRequested();
}

void AppBridge::loadingFrame() {
    if (!loading_shown_) {
        loading_shown_ = true;
        Q_EMIT loadingShown();
    }
}

void AppBridge::shellReady() {
    if (!shell_loaded_) {
        shell_loaded_ = true;
        Q_EMIT shellLoaded();
    }
}

void AppBridge::shellFailed() {
    setStatus("The app's screens could not be loaded (QML error; see the log).", 0.0);
    Q_EMIT failed();
}

void AppBridge::shellFrame() {
    if (shell_loaded_ && !shell_shown_) {
        shell_shown_ = true;
        Q_EMIT shellShown();
    }
}

void AppBridge::back() {
    if (screen_ != "simulate")
        setScreen("simulate");
    else
        Q_EMIT exitRequested();
}

// --------------------------------------------------------------- Scenarios
QVariantList ScenarioBridge::items() const {
    if (items_.isEmpty())
        for (const auto& key : pendulum::scenarioKeys()) {
            const auto cfg = pendulum::scenario(key);
            items_ << QVariantMap{{"key", QString::fromStdString(key)},
                                  {"name", QString::fromStdString(cfg.name)},
                                  {"description", QString::fromStdString(cfg.description)}};
        }
    return items_;
}

void ScenarioBridge::load(const QString& key) {
    try {
        sim_->load(pendulum::scenario(key.toStdString()));
    } catch (const std::out_of_range&) {
    }
}

}  // namespace app
