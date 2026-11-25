#pragma once
// QML singletons `Settings` and `App`, context object `scenarios`
// (quick/backend/settings.py, shell.py). Same QSettings keys as the Python app.
#include <QObject>
#include <QSettings>
#include <QVariantList>

namespace app {

class SimulationBridge;

class SettingsBridge : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString theme READ theme WRITE setTheme NOTIFY themeChanged)
    Q_PROPERTY(QVariantList themes READ themes CONSTANT)
    Q_PROPERTY(bool isDark READ isDark NOTIFY darkChanged)
    Q_PROPERTY(QString frameRate READ frameRate WRITE setFrameRate NOTIFY frameRateChanged)
    Q_PROPERTY(QVariantList frameRates READ frameRates CONSTANT)
    Q_PROPERTY(int graphRate READ graphRate WRITE setGraphRate NOTIFY graphRateChanged)
    Q_PROPERTY(QVariantList graphRates READ graphRates CONSTANT)
    Q_PROPERTY(QString physics READ physics WRITE setPhysics NOTIFY physicsChanged)
    Q_PROPERTY(bool overlay READ overlay WRITE setOverlay NOTIFY overlayChanged)
public:
    explicit SettingsBridge(QObject* parent = nullptr);
    QString theme() const { return theme_; }
    void setTheme(const QString& v);
    void overrideTheme(const QString& v);  ///< this run only (command line)
    QVariantList themes() const { return {"system", "light", "dark"}; }
    bool isDark() const;
    QString frameRate() const { return frame_rate_; }
    void setFrameRate(const QString& v);
    QVariantList frameRates() const { return {"auto", "120", "60", "40", "30"}; }
    int graphRate() const { return graph_rate_; }
    void setGraphRate(int v);
    QVariantList graphRates() const { return {20, 25, 30}; }
    QString physics() const { return QStringLiteral("thread"); }
    void setPhysics(const QString&) {}
    bool overlay() const { return overlay_; }
    void setOverlay(bool on);
Q_SIGNALS:
    void themeChanged();
    void darkChanged();
    void frameRateChanged();
    void graphRateChanged();
    void physicsChanged();
    void overlayChanged();
private:
    bool systemDark() const;
    QSettings store_;
    QString theme_, frame_rate_;
    int graph_rate_ = 25;
    bool overlay_ = false;
    bool system_dark_ = false;
};

class AppBridge : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString status READ status NOTIFY statusChanged)
    Q_PROPERTY(double progress READ progress NOTIFY statusChanged)
    Q_PROPERTY(QString previousReport READ previousReport NOTIFY previousChanged)
    Q_PROPERTY(bool previousCopied READ previousCopied NOTIFY previousChanged)
    Q_PROPERTY(bool ready READ ready NOTIFY readyChanged)
    Q_PROPERTY(QString screen READ screen WRITE setScreen NOTIFY screenChanged)
    Q_PROPERTY(QVariantList screens READ screens CONSTANT)
public:
    using QObject::QObject;
    void setStatus(const QString& text, double progress);
    QString status() const { return status_; }
    double progress() const { return progress_; }
    QString previousReport() const { return previous_; }
    bool previousCopied() const { return copied_; }
    bool ready() const { return ready_; }
    void setReady();
    QString screen() const { return screen_; }
    void setScreen(const QString& key);
    QVariantList screens() const;
    bool shown() const { return shell_shown_; }

    Q_INVOKABLE void copyPreviousReport();
    Q_INVOKABLE void continueStart();
    Q_INVOKABLE void loadingFrame();
    Q_INVOKABLE void shellReady();
    Q_INVOKABLE void shellFailed();
    Q_INVOKABLE void shellFrame();
    Q_INVOKABLE void back();
Q_SIGNALS:
    void statusChanged();
    void readyChanged();
    void screenChanged();
    void loadingShown();
    void shellLoaded();
    void shellShown();
    void exitRequested();
    void failed();
    void continueRequested();
    void previousChanged();
private:
    QString status_ = "Starting…";
    double progress_ = 0.1;
    bool ready_ = false;
    QString screen_ = "simulate";
    bool loading_shown_ = false, shell_loaded_ = false, shell_shown_ = false;
    QString previous_;
    bool copied_ = false;
};

class ScenarioBridge : public QObject {
    Q_OBJECT
    Q_PROPERTY(QVariantList items READ items CONSTANT)
public:
    explicit ScenarioBridge(SimulationBridge* sim, QObject* parent = nullptr) : QObject(parent), sim_(sim) {}
    QVariantList items() const;
    Q_INVOKABLE void load(const QString& key);
private:
    SimulationBridge* sim_;
    mutable QVariantList items_;
};

}  // namespace app
