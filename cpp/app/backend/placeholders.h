#pragma once
// Context objects of the screens still being ported (Controller, Model, Auto-tune,
// Experiments): the few properties the shell reads, so the Simulate, Graphs and Settings
// screens work while the others are ported (Phase 5 stages).
#include <QObject>

namespace app {

class Placeholder : public QObject {
    Q_OBJECT
    Q_PROPERTY(bool busy READ no CONSTANT)
    Q_PROPERTY(bool running READ no CONSTANT)
public:
    using QObject::QObject;
    bool no() const { return false; }
};

}  // namespace app
