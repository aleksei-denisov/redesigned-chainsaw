#include "formmodel.h"

#include <QTimer>
#include <algorithm>
#include <cmath>

#include "pendulum/session/sliders.hpp"

namespace app {

namespace ps = pendulum::session;

namespace {
const char* const kRoles[] = {"rowType", "key",   "label", "kind", "unit",     "decimals", "minimum", "maximum", "mapping",
                              "step",    "hint",  "options", "value", "editable", "section", "expanded", "note"};
constexpr int kRoleCount = sizeof(kRoles) / sizeof(kRoles[0]);
enum Role : int {
    RowType = Qt::UserRole + 1, Key, Label, Kind, Unit, Decimals, Minimum, Maximum, Mapping,
    Step, Hint, Options, Value, Editable, SectionRole, Expanded, Note
};
QString qs(const std::string& s) { return QString::fromStdString(s); }
}  // namespace

QVariant toQVariant(const ps::Value& v) {
    if (const auto* d = std::get_if<double>(&v)) return *d;
    if (const auto* b = std::get_if<bool>(&v)) return *b;
    return qs(std::get<std::string>(v));
}

FormModel::FormModel(ps::Form& form, EditFn onEdit, QObject* parent)
    : QAbstractListModel(parent), form_(form), on_edit_(std::move(onEdit)) {
    refresh();
}

bool FormModel::onPage(const std::string& key) const {
    if (pages_.empty()) return true;
    return std::any_of(pages_.begin(), pages_.end(), [&](const std::string& p) {
        if (!p.empty() && p.back() == '*') return key.rfind(p.substr(0, p.size() - 1), 0) == 0;
        return key == p;
    });
}

bool FormModel::isExpanded(const ps::Section& s) {
    auto it = expanded_.find(s.key);
    if (it == expanded_.end()) it = expanded_.emplace(s.key, s.expanded).first;
    return it->second;
}

std::vector<FormModel::Row> FormModel::build() {
    std::vector<Row> rows;
    for (const auto& s : form_.sections()) {
        if ((visible_ && !visible_(s.key)) || !onPage(s.key)) continue;
        rows.push_back({true, &s, nullptr, s.key, "s:" + s.key});
        if (isExpanded(s))
            for (const auto& f : s.fields) rows.push_back({false, &s, &f, s.key, "f:" + f.key});
    }
    return rows;
}

void FormModel::refresh(bool reset) {
    std::vector<Row> fresh = build();
    if (reset || rows_.empty() || fresh.empty()) {
        beginResetModel();
        rows_ = std::move(fresh);
        endResetModel();
        return;
    }
    // Old rows may point into sections the form has rebuilt: point them at the new ones first
    // (views read kept rows while rows are removed and inserted); rows about to go read as empty.
    {
        std::map<std::string, const Row*> by_id;
        for (const auto& r : fresh) by_id[r.id] = &r;
        for (auto& r : rows_) {
            auto it = by_id.find(r.id);
            r.sec = it == by_id.end() ? nullptr : it->second->sec;
            r.field = it == by_id.end() ? nullptr : it->second->field;
        }
    }
    const std::size_t n_old = rows_.size(), n_new = fresh.size();
    std::size_t head = 0;
    while (head < std::min(n_old, n_new) && rows_[head].id == fresh[head].id) ++head;
    std::size_t tail = 0;
    while (tail < std::min(n_old, n_new) - head && rows_[n_old - 1 - tail].id == fresh[n_new - 1 - tail].id) ++tail;
    if (n_old - tail > head) {
        beginRemoveRows({}, static_cast<int>(head), static_cast<int>(n_old - tail - 1));
        rows_.erase(rows_.begin() + static_cast<long>(head), rows_.begin() + static_cast<long>(n_old - tail));
        endRemoveRows();
    }
    if (n_new - tail > head) {
        beginInsertRows({}, static_cast<int>(head), static_cast<int>(n_new - tail - 1));
        rows_ = fresh;
        endInsertRows();
    }
    // Rows kept point into sections that may have been rebuilt (loops): take the new pointers.
    rows_ = std::move(fresh);
    if (!rows_.empty()) Q_EMIT dataChanged(index(0), index(static_cast<int>(rows_.size()) - 1), {});
}

void FormModel::updateValues() {
    if (!rows_.empty())
        Q_EMIT dataChanged(index(0), index(static_cast<int>(rows_.size()) - 1), {Value, Editable, Note});
}

QHash<int, QByteArray> FormModel::roleNames() const {
    QHash<int, QByteArray> r;
    for (int i = 0; i < kRoleCount; ++i) r[Qt::UserRole + 1 + i] = kRoles[i];
    return r;
}

int FormModel::rowCount(const QModelIndex& parent) const { return parent.isValid() ? 0 : static_cast<int>(rows_.size()); }

QVariant FormModel::data(const QModelIndex& index, int role) const {
    if (!index.isValid() || index.row() < 0 || index.row() >= static_cast<int>(rows_.size())) return {};
    const Row& row = rows_[static_cast<std::size_t>(index.row())];
    if (row.section ? row.sec == nullptr : row.field == nullptr) return {};
    if (row.section) {
        const ps::Section& s = *row.sec;
        switch (role) {
            case RowType: return QStringLiteral("section");
            case Key: return qs(s.key);
            case Label: return qs(s.title);
            case SectionRole: return qs(s.key);
            case Expanded: {
                auto it = expanded_.find(s.key);
                return it == expanded_.end() ? true : it->second;
            }
            case Note: return qs(note_ ? note_(s.key) : s.note);
            default: return {};
        }
    }
    const ps::Field& f = *row.field;
    switch (role) {
        case RowType: return QStringLiteral("field");
        case Key: return qs(f.key);
        case Label: return qs(f.label);
        case Kind: return qs(f.kind);
        case Unit: return qs(f.unit);
        case Decimals: return f.decimals;
        case Minimum: return f.minimum;
        case Maximum: return f.maximum;
        case Mapping: return qs(f.mapping);
        case Step: return f.step.value_or(0.0);
        case Hint: return qs(f.hint);
        case Options: {
            QVariantList list;
            for (const auto& [v, t] : f.options) list.push_back(QVariantMap{{"value", qs(v)}, {"text", qs(t)}});
            return list;
        }
        case Value: return form_.values().count(f.key) ? toQVariant(form_.get(f.key)) : QVariant();
        case Editable: return editable_ ? editable_(f.key) : true;
        case SectionRole: return qs(row.section_key);
        case Expanded: return true;
        case Note: return QString();
        default: return {};
    }
}

ps::Value FormModel::toValue(const ps::Field& f, const QVariant& v) const {
    if (f.kind == "switch") return v.toBool();
    if (f.kind == "choice") return v.toString().toStdString();
    return v.toDouble();
}

void FormModel::apply(const std::string& key, const ps::Value& value) {
    if (!form_.has(key)) return;
    on_edit_(key, value);
    Q_EMIT edited(qs(key), toQVariant(value));
}

// Edits from QML take effect once control is back in the event loop: an edit can rebuild the
// rows (loops, segments), and rebuilding delegates inside the QML handler that asked for it
// crashes the view.
void FormModel::setValue(const QString& key, const QVariant& value) {
    const std::string k = key.toStdString();
    if (!form_.has(k)) return;
    const ps::Value v = toValue(form_.field(k), value);
    QTimer::singleShot(0, this, [this, k, v] { apply(k, v); });
}

void FormModel::setExpanded(const QString& section, bool on) {
    const std::string k = section.toStdString();
    QTimer::singleShot(0, this, [this, k, on] {
        auto it = expanded_.find(k);
        if (it == expanded_.end() || it->second != on) {
            expanded_[k] = on;
            refresh();
        }
    });
}

void FormModel::setSections(const QVariantList& keys) {
    std::vector<std::string> pages;
    for (const auto& k : keys) pages.push_back(k.toString().toStdString());
    if (pages != pages_) {
        pages_ = std::move(pages);
        refresh(true);  // another page: from its top
    }
}

QVariant FormModel::value(const QString& key) const {
    const std::string k = key.toStdString();
    return form_.values().count(k) ? toQVariant(form_.get(k)) : QVariant();
}

double FormModel::position(const QString& key, double value) const {
    const std::string k = key.toStdString();
    if (!form_.has(k)) return 0.0;
    const ps::Field& f = form_.field(k);
    const double t = ps::toPosition(value, f.minimum, f.maximum, f.mapping, f.decimals);
    return std::isfinite(t) ? std::clamp(t, 0.0, 1.0) : 0.0;
}

namespace {
double roundTo(double v, int decimals) {
    const double s = std::pow(10.0, decimals);
    return std::nearbyint(v * s) / s;  // Python round(): half to even
}
}  // namespace

double FormModel::valueAt(const QString& key, double t) const {
    const std::string k = key.toStdString();
    if (!form_.has(k)) return 0.0;
    const ps::Field& f = form_.field(k);
    const double v = ps::fromPosition(std::clamp(t, 0.0, 1.0), f.minimum, f.maximum, f.mapping, f.decimals);
    return std::clamp(roundTo(v, f.decimals), f.minimum, f.maximum);
}

double FormModel::nudged(const QString& key, int direction) const {
    const std::string k = key.toStdString();
    if (!form_.has(k) || !form_.values().count(k)) return 0.0;
    const auto* v = std::get_if<double>(&form_.get(k));
    if (!v) return 0.0;
    const ps::Field& f = form_.field(k);
    const double n = ps::nudge(*v, direction, f.minimum, f.maximum, f.mapping, f.decimals, f.step);
    return std::clamp(roundTo(n, f.decimals), f.minimum, f.maximum);
}

}  // namespace app
