#pragma once
// QML context object `sim` (quick/backend/simulation.py): the running simulation – state per
// frame, read-outs, run control. Owns the edited configuration (a shared Session) and the
// physics host; every edit made anywhere in the app ends in applyOperations().
#include <QObject>
#include <QVariantList>
#include <memory>

#include "pendulum/session/session.hpp"
#include "simhost.h"

namespace app {

class SimulationBridge : public QObject {
    Q_OBJECT
    Q_PROPERTY(int segments READ segments NOTIFY structureChanged)
    Q_PROPERTY(QVariantList lengths READ lengths NOTIFY structureChanged)
    Q_PROPERTY(bool hasCart READ hasCart NOTIFY structureChanged)
    Q_PROPERTY(double trackLength READ trackLength NOTIFY structureChanged)
    Q_PROPERTY(double maxOutput READ maxOutput NOTIFY structureChanged)
    Q_PROPERTY(QString scenarioName READ scenarioName NOTIFY configChanged)
    Q_PROPERTY(QString scenarioDescription READ scenarioDescription NOTIFY configChanged)
    Q_PROPERTY(bool processAvailable READ processAvailable CONSTANT)
    Q_PROPERTY(QVariantList speeds READ speeds CONSTANT)
    Q_PROPERTY(double cartX READ cartX NOTIFY frame)
    Q_PROPERTY(double th1 READ th1 NOTIFY frame)
    Q_PROPERTY(double th2 READ th2 NOTIFY frame)
    Q_PROPERTY(double th3 READ th3 NOTIFY frame)
    Q_PROPERTY(double simTime READ simTime NOTIFY frame)
    Q_PROPERTY(double applied READ applied NOTIFY frame)
    Q_PROPERTY(double tipForce READ tipForce NOTIFY frame)
    Q_PROPERTY(QString angleText READ angleText NOTIFY readoutsChanged)
    Q_PROPERTY(QString rateText READ rateText NOTIFY readoutsChanged)
    Q_PROPERTY(QString cartText READ cartText NOTIFY readoutsChanged)
    Q_PROPERTY(QString forceText READ forceText NOTIFY readoutsChanged)
    Q_PROPERTY(QString timeText READ timeText NOTIFY readoutsChanged)
    Q_PROPERTY(QString fpsText READ fpsText NOTIFY readoutsChanged)
    Q_PROPERTY(QString speedText READ speedText NOTIFY readoutsChanged)
    Q_PROPERTY(QString energyText READ energyText NOTIFY readoutsChanged)
    Q_PROPERTY(double fps READ fps NOTIFY readoutsChanged)
    Q_PROPERTY(bool running READ running WRITE setRunning NOTIFY runningChanged)
    Q_PROPERTY(bool paused READ paused NOTIFY runningChanged)
    Q_PROPERTY(double speed READ speed WRITE setSpeed NOTIFY speedChanged)
    Q_PROPERTY(double pushForce READ pushForce WRITE setPushForce NOTIFY pushForceChanged)
    Q_PROPERTY(QString physics READ physics WRITE setPhysics NOTIFY physicsChanged)

public:
    explicit SimulationBridge(const pendulum::AppConfig& config, QObject* parent = nullptr);

    pendulum::session::Session session;
    SimHost& host() { return *host_; }

    int segments() const { return n_; }
    QVariantList lengths() const;
    bool hasCart() const { return session.config.plant.hasCart(); }
    double trackLength() const { return session.config.plant.track_length; }
    double maxOutput() const { return session.config.plant.actuator.max_output; }
    QString scenarioName() const { return QString::fromStdString(session.config.name); }
    QString scenarioDescription() const { return QString::fromStdString(session.config.description); }
    bool processAvailable() const { return false; }  // C++: physics always on its own thread
    QVariantList speeds() const;
    double cartX() const { return cart_; }
    double th1() const { return th_[0]; }
    double th2() const { return th_[1]; }
    double th3() const { return th_[2]; }
    double simTime() const { return t_; }
    double applied() const { return applied_; }
    double tipForce() const { return tip_; }
    QString angleText() const { return texts_[0]; }
    QString rateText() const { return texts_[1]; }
    QString cartText() const { return texts_[2]; }
    QString forceText() const { return texts_[3]; }
    QString timeText() const { return texts_[4]; }
    QString fpsText() const { return texts_[5]; }
    QString speedText() const { return texts_[6]; }
    QString energyText() const { return texts_[7]; }
    double fps() const { return fps_; }
    bool running() const { return running_; }
    void setRunning(bool on);
    bool paused() const { return !running_ && t_ > 0.0; }
    double speed() const { return speed_; }
    void setSpeed(double v);
    double pushForce() const { return session.config.push_force; }
    void setPushForce(double v);
    QString physics() const { return QStringLiteral("thread"); }
    void setPhysics(const QString&) {}

    // state of the last frame (trail, value tiles)
    long epoch() const { return epoch_; }
    const std::array<double, 3>& angles() const { return th_; }
    const std::vector<double>& lengthsVec() const { return lens_; }
    double frameGapMs() const { return gap_ms_; }

    /// One displayed frame: read the interpolated state and notify QML.
    Q_INVOKABLE void advance();
    Q_INVOKABLE void toggle() { setRunning(!running_); }
    Q_INVOKABLE void start() { setRunning(true); }
    Q_INVOKABLE void pause() { setRunning(false); }
    Q_INVOKABLE void reset();
    Q_INVOKABLE void restart();
    Q_INVOKABLE void step();
    Q_INVOKABLE void push(int direction);
    Q_INVOKABLE void pushAt(int direction, int segment, double ratio);
    Q_INVOKABLE void drag(double offset);
    Q_INVOKABLE void dragAt(double offset, int segment, double ratio);
    Q_INVOKABLE void endDrag();

    void applyOperations(const std::vector<pendulum::session::Operation>& ops) { host_->sendOps(ops); }
    bool load(const pendulum::AppConfig& config);  ///< whole configuration; false if invalid
    void structureEdited();                         ///< segments / base changed through an edit
    void suspend();
    void resume();

Q_SIGNALS:
    void frame();
    void readoutsChanged();
    void runningChanged();
    void structureChanged();
    void speedChanged();
    void pushForceChanged();
    void physicsChanged();
    void configChanged();
    void controllerChanged();
    void plantChanged(bool structural);
    void message(const QString& kind, const QString& text);

private:
    void updateStructure();
    void readouts();

    std::unique_ptr<SimHost> host_;
    int n_ = 1;
    std::vector<double> lens_;
    double cart_ = 0, t_ = 0, applied_ = 0, tip_ = 0, omega_ = 0;
    std::array<double, 3> th_{};
    long epoch_ = 0;
    bool running_ = false, suspended_running_ = false;
    double speed_ = 1.0;
    double readout_due_ = 0.0;
    double fps_ = 0.0, gap_ms_ = 0.0, last_frame_ = 0.0, fps_second_ = 0.0;
    int fps_count_ = 0;
    QString texts_[8];
    Sample sample_;
};

}  // namespace app
