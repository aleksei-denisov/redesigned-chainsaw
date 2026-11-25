#pragma once
// A shared parameter form (core session/forms) as a QML list model (quick/backend/formmodel.py).
//
// Rows are section headers and fields, in screen order; hidden sections (the cart on a fixed
// pivot, unused segments) are left out. QML shows each row with a delegate chosen by `kind`
// and edits with setValue(); the owner (a bridge) decides what an edit means and calls
// refresh() / updateValues() afterwards. A rebuild changes only the rows that differ, so the
// list keeps its scroll position; a sub-page (setSections) starts at the top.
#include <QAbstractListModel>
#include <QVariant>
#include <functional>
#include <map>
#include <string>
#include <vector>

#include "pendulum/session/forms.hpp"

namespace app {

class FormModel : public QAbstractListModel {
    Q_OBJECT
public:
    using EditFn = std::function<void(const std::string&, const pendulum::session::Value&)>;
    using SectionFn = std::function<bool(const std::string&)>;
    using KeyFn = std::function<bool(const std::string&)>;
    using NoteFn = std::function<std::string(const std::string&)>;

    FormModel(pendulum::session::Form& form, EditFn onEdit, QObject* parent = nullptr);
    void setVisible(SectionFn f) { visible_ = std::move(f); }
    void setEditable(KeyFn f) { editable_ = std::move(f); }
    void setNote(NoteFn f) { note_ = std::move(f); }
    void setExpandedDefault(const std::string& section, bool on) { expanded_[section] = on; }

    /// Rebuild the rows (sections shown or folded, loops rebuilt …); `reset` rebuilds all.
    void refresh(bool reset = false);
    /// Values changed from outside (configuration loaded, LQR seeding, live outputs …).
    void updateValues();
    /// An edit, at once (tests, C++ callers).
    void apply(const std::string& key, const pendulum::session::Value& value);

    int rowCount(const QModelIndex& parent = {}) const override;
    QVariant data(const QModelIndex& index, int role) const override;
    QHash<int, QByteArray> roleNames() const override;

    Q_INVOKABLE void setValue(const QString& key, const QVariant& value);
    Q_INVOKABLE void setExpanded(const QString& section, bool on);
    Q_INVOKABLE void setSections(const QVariantList& keys);
    Q_INVOKABLE QVariant value(const QString& key) const;
    Q_INVOKABLE double position(const QString& key, double value) const;
    Q_INVOKABLE double valueAt(const QString& key, double t) const;
    Q_INVOKABLE double nudged(const QString& key, int direction) const;

Q_SIGNALS:
    void edited(const QString& key, const QVariant& value);

private:
    struct Row {
        bool section;
        const pendulum::session::Section* sec;
        const pendulum::session::Field* field;
        std::string section_key;
        std::string id;  ///< "s:<section>" / "f:<field>" – kept as text: a rebuilt form frees the old sections
    };
    std::vector<Row> build();
    bool onPage(const std::string& key) const;
    bool isExpanded(const pendulum::session::Section& s);
    pendulum::session::Value toValue(const pendulum::session::Field& f, const QVariant& v) const;

    pendulum::session::Form& form_;
    EditFn on_edit_;
    SectionFn visible_;
    KeyFn editable_;
    NoteFn note_;
    std::map<std::string, bool> expanded_;
    std::vector<std::string> pages_;
    std::vector<Row> rows_;
};

QVariant toQVariant(const pendulum::session::Value& v);

}  // namespace app
