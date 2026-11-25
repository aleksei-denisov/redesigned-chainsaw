#include "panel.h"

#include <QJsonDocument>
#include <QUuid>
#include <cmath>
#include <numbers>

#include "pendulum/simulation/telemetry.hpp"
#include "simulation.h"

namespace app {

namespace {
const QStringList kTypes = {"value", "graph", "status"};
const QStringList kSizes = {"half", "full"};
const QStringList kHeights = {"small", "medium", "large", "fill"};
const QStringList kScales = {"auto", "symmetric", "fixed"};
const QStringList kColors = {"text", "accent", "angle", "segment2", "segment3", "success",
                             "palette0", "palette1", "palette2", "palette3", "palette4"};
const QStringList kSmoothings = {"off", "light", "medium", "strong"};
const QString kDefaultName = "Default";
const char* kKeyCurrent = "quick/panel/current";
const char* kKeyName = "quick/panel/current_name";
const char* kKeySaved = "quick/panel/saved";

QString newId() { return QUuid::createUuid().toString(QUuid::Id128).left(8); }

double number(const QVariant& v, double def) {
    bool ok = false;
    const double f = v.toDouble(&ok);
    return ok && f == f && std::abs(f) < 1e12 ? f : def;
}
}  // namespace

// --------------------------------------------------------------- PanelModel
QVariantMap PanelModel::defaults(const QString& kind, const QString& smoothing) {
    if (kind == "value")
        return {{"type", "value"}, {"signal", "theta1"}, {"size", "half"}, {"color", "text"}, {"angle_unit", "deg"}};
    if (kind == "graph")
        return {{"type", "graph"},       {"signals", QVariantList{"theta1"}}, {"window", 10},    {"scale", "auto"},
                {"fixed", QVariantList{}}, {"smoothing", smoothing},          {"height", "fill"}, {"angle_unit", "deg"}};
    return {{"type", "status"}, {"show_scenario", true}, {"show_controller", true}, {"show_gains", true}};
}

std::optional<QVariantMap> PanelModel::cleanTile(const QVariant& v, const QString& smoothing) {
    const QVariantMap tile = v.toMap();
    if (tile.isEmpty() || !kTypes.contains(tile.value("type").toString())) return std::nullopt;
    const QString type = tile.value("type").toString();
    QVariantMap out = defaults(type, smoothing);
    for (auto it = out.begin(); it != out.end(); ++it)
        if (tile.contains(it.key())) it.value() = tile.value(it.key());
    auto choice = [&](const char* key, const QStringList& allowed, const QString& def) {
        const QString s = out.value(key).toString();
        out[key] = allowed.contains(s) ? s : def;
    };
    if (type == "value") {
        out["signal"] = out.value("signal").toString();
        choice("size", kSizes, "half");
        choice("color", kColors, "text");
        choice("angle_unit", {"deg", "rad"}, "deg");
    } else if (type == "graph") {
        QStringList sigs;
        if (out.value("signals").typeId() == QMetaType::QVariantList || out.value("signals").typeId() == QMetaType::QStringList)
            for (const auto& s : out.value("signals").toList())
                if (!sigs.contains(s.toString())) sigs << s.toString();
        while (sigs.size() > graph::kMaxSignals) sigs.removeLast();
        if (sigs.isEmpty()) sigs << "theta1";
        out["signals"] = QVariant(QVariantList(sigs.begin(), sigs.end()));
        const double w = number(out.value("window"), 10);
        int best = graph::kWindowsS[0];
        for (int c : graph::kWindowsS)
            if (std::abs(c - w) < std::abs(best - w)) best = c;
        out["window"] = best;
        choice("scale", kScales, "auto");
        QVariantList good;
        const QVariantList fixed = out.value("fixed").toList();
        for (int i = 0; i < fixed.size() && i < 2; ++i) {
            const QVariantList pair = fixed[i].toList();
            if (pair.size() != 2) continue;
            const double lo = number(pair[0], 0.0), hi = number(pair[1], 1.0);
            good << QVariant(lo != hi ? QVariantList{std::min(lo, hi), std::max(lo, hi)} : QVariantList{lo - 1.0, lo + 1.0});
        }
        out["fixed"] = good;
        choice("smoothing", kSmoothings, "off");
        choice("height", kHeights, "fill");
        choice("angle_unit", {"deg", "rad"}, "deg");
    } else {
        for (const char* k : {"show_scenario", "show_controller", "show_gains"}) out[k] = out.value(k).toBool();
    }
    const QString id = tile.value("id").toString();
    out["id"] = id.isEmpty() ? newId() : id;
    return out;
}

QVariantList PanelModel::clean(const QVariant& tiles, const QString& smoothing) {
    QVariantList out;
    QSet<QString> seen;
    const QVariantList in = tiles.toList();
    for (int i = 0; i < in.size() && i < kMaxTiles; ++i)
        if (auto t = cleanTile(in[i], smoothing)) {
            if (seen.contains((*t)["id"].toString())) (*t)["id"] = newId();  // ids must be unique
            seen.insert((*t)["id"].toString());
            out << *t;
        }
    return out.isEmpty() ? defaultLayout() : out;
}

QVariantList PanelModel::defaultLayout() {
    const QVariantList d = {
        QVariantMap{{"type", "value"}, {"signal", "theta1"}, {"size", "half"}, {"color", "angle"}},
        QVariantMap{{"type", "value"}, {"signal", "omega1"}, {"size", "half"}, {"color", "text"}},
        QVariantMap{{"type", "value"}, {"signal", "x"}, {"size", "half"}, {"color", "accent"}},
        QVariantMap{{"type", "value"}, {"signal", "actuator"}, {"size", "half"}, {"color", "text"}},
        QVariantMap{{"type", "graph"}, {"signals", QVariantList{"theta1", "x"}}, {"window", 10}, {"scale", "auto"},
                    {"smoothing", "off"}, {"height", "fill"}}};
    QVariantList out;
    for (const auto& t : d) out << *cleanTile(t);
    return out;
}

std::vector<std::vector<int>> PanelModel::rows(const QVariantList& tiles, int columns) {
    std::vector<std::vector<int>> out;
    std::vector<int> cur;
    for (int i = 0; i < tiles.size(); ++i) {
        const QVariantMap t = tiles[i].toMap();
        if (t["type"] == "value" && t["size"] == "half") {
            cur.push_back(i);
            if (static_cast<int>(cur.size()) == columns) {
                out.push_back(cur);
                cur.clear();
            }
            continue;
        }
        if (!cur.empty()) {
            out.push_back(cur);
            cur.clear();
        }
        out.push_back({i});
    }
    if (!cur.empty()) out.push_back(cur);
    return out;
}

PanelModel::PanelModel(QSettings& store) : store_(store) {
    tiles = clean(loadJson(kKeyCurrent));
    name = store_.value(kKeyName, kDefaultName).toString();
    if (name.isEmpty()) name = kDefaultName;
}

QVariant PanelModel::loadJson(const QString& key) const {
    const QVariant raw = store_.value(key);
    if (!raw.isValid() || raw.toString().isEmpty()) return {};
    if (raw.typeId() == QMetaType::QString || raw.typeId() == QMetaType::QByteArray)
        return QJsonDocument::fromJson(raw.toString().toUtf8()).toVariant();
    return raw;
}

void PanelModel::save() {
    store_.setValue(kKeyCurrent, QString::fromUtf8(QJsonDocument::fromVariant(tiles).toJson(QJsonDocument::Compact)));
    store_.setValue(kKeyName, name);
}

int PanelModel::index(const QString& id) const {
    for (int i = 0; i < tiles.size(); ++i)
        if (tiles[i].toMap().value("id").toString() == id) return i;
    return -1;
}

QString PanelModel::add(const QString& kind, const QString& smoothing) {
    if (!kTypes.contains(kind) || tiles.size() >= kMaxTiles) return {};
    const auto t = cleanTile(defaults(kind, smoothing), smoothing);
    tiles << *t;
    save();
    return (*t)["id"].toString();
}

void PanelModel::remove(const QString& id) {
    const int i = index(id);
    if (i >= 0) {
        tiles.removeAt(i);
        save();
    }
}

void PanelModel::move(const QString& id, int to) {
    const int i = index(id);
    if (i < 0) return;
    to = std::max(0, std::min(to, static_cast<int>(tiles.size()) - 1));
    if (to != i) {
        tiles.move(i, to);
        save();
    }
}

void PanelModel::update(const QString& id, const QVariantMap& changes) {
    const int i = index(id);
    if (i < 0) return;
    QVariantMap merged = tiles[i].toMap();
    for (auto it = changes.begin(); it != changes.end(); ++it)
        if (it.key() != "id" && it.key() != "type") merged[it.key()] = it.value();
    if (auto t = cleanTile(merged)) {
        tiles[i] = *t;
        save();
    }
}

void PanelModel::reset() {
    tiles = defaultLayout();
    name = kDefaultName;
    save();
}

QVariantMap PanelModel::saved() const { return loadJson(kKeySaved).toMap(); }

QStringList PanelModel::names() const {
    QStringList n = saved().keys();
    std::sort(n.begin(), n.end(), [](const QString& a, const QString& b) { return a.toLower() < b.toLower(); });
    return n;
}

bool PanelModel::saveAs(const QString& raw) {
    const QString nm = raw.simplified().left(40);
    if (nm.isEmpty()) return false;
    QVariantMap data = saved();
    data[nm] = tiles;
    store_.setValue(kKeySaved, QString::fromUtf8(QJsonDocument::fromVariant(data).toJson(QJsonDocument::Compact)));
    name = nm;
    save();
    return true;
}

bool PanelModel::load(const QString& nm) {
    const QVariantMap data = saved();
    if (nm == kDefaultName && !data.contains(nm)) {
        reset();
        return true;
    }
    if (!data.contains(nm)) return false;
    tiles = clean(data[nm]);
    name = nm;
    save();
    return true;
}

void PanelModel::remove_layout(const QString& nm) {
    QVariantMap data = saved();
    if (data.remove(nm))
        store_.setValue(kKeySaved, QString::fromUtf8(QJsonDocument::fromVariant(data).toJson(QJsonDocument::Compact)));
}

// ------------------------------------------------------------- status lines
namespace {
QString gain(double v) {
    if (v == 0) return "0";
    return std::abs(v) < 1000 ? QString::asprintf("%.3g", v) : QString::asprintf("%.0f", v);
}

QVariantList statusLines(const pendulum::AppConfig& cfg, const QVariantMap& tile) {
    QVariantList rows;
    const auto& c = cfg.controller;
    if (tile.value("show_scenario", true).toBool())
        rows << QVariantMap{{"label", "Scenario"}, {"value", QString::fromStdString(cfg.name)}};
    if (tile.value("show_controller", true).toBool()) {
        int loops = 0;
        for (const auto& l : c.loops) loops += l.enabled;
        const QString kind = !c.enabled ? "off" : loops > 1 ? QString::number(loops) + "-loop PID" : "PID";
        rows << QVariantMap{{"label", "Controller"},
                            {"value", kind + QString::asprintf(" · %g Hz · %g … %g N", c.frequency_hz, c.output_min,
                                                               c.output_max)}};
    }
    if (tile.value("show_gains", true).toBool())
        for (const auto& l : c.loops) {
            if (!l.enabled) continue;
            QStringList terms;
            if (l.p_enabled) terms << "Kp " + gain(l.kp);
            if (l.i_enabled) terms << "Ki " + gain(l.ki);
            if (l.d_enabled) terms << "Kd " + gain(l.kd);
            rows << QVariantMap{{"label", QString::fromStdString(l.name)},
                                {"value", terms.isEmpty() ? QString("—") : terms.join("  ")}};
        }
    return rows;
}
}  // namespace

// --------------------------------------------------------------- PanelBridge
PanelBridge::PanelBridge(SimulationBridge* sim, GraphsBridge* graphs, QObject* parent)
    : QObject(parent), sim_(sim), graphs_(graphs), model_(store_) {
    notify_.setSingleShot(true);
    notify_.setInterval(0);
    connect(&notify_, &QTimer::timeout, this, &PanelBridge::tilesChanged);
    connect(sim, &SimulationBridge::structureChanged, this, &PanelBridge::tilesEdited);
    connect(sim, &SimulationBridge::controllerChanged, this, &PanelBridge::tilesEdited);
    connect(sim, &SimulationBridge::configChanged, this, &PanelBridge::updateStatus);
    connect(graphs, &GraphsBridge::catalogueChanged, this, &PanelBridge::tilesEdited);
    tilesEdited();
}

graph::Spec PanelBridge::spec(const QVariantMap& t) {
    graph::Spec s;
    for (const auto& v : t["signals"].toList()) s.signals.push_back(v.toString().toStdString());
    s.window_s = t["window"].toDouble();
    s.scale = t["scale"].toString().toStdString();
    for (const auto& p : t["fixed"].toList()) {
        const auto l = p.toList();
        if (l.size() == 2) s.fixed.emplace_back(l[0].toDouble(), l[1].toDouble());
    }
    s.smoothing = t["smoothing"].toString().toStdString();
    s.angle_unit = t["angle_unit"].toString().toStdString();
    return s;
}

QVariantList PanelBridge::tiles() const {
    QVariantList out;
    const auto& cat = graphs_->catalogue();
    for (const auto& v : model_.tiles) {
        QVariantMap d = v.toMap();
        if (d["type"] == "value") {
            const auto it = cat.find(d["signal"].toString().toStdString());
            if (it == cat.end()) {
                d["name"] = d["signal"];
                d["unit"] = "";
                d["available"] = false;
            } else {
                const auto disp = it->second.display(d["angle_unit"].toString().toStdString());
                d["name"] = QString::fromStdString(it->second.name.empty() ? it->second.label : it->second.name);
                d["unit"] = QString::fromStdString(disp.unit);
                d["available"] = true;
            }
        } else if (d["type"] == "graph") {
            QVariantList missing;
            for (const auto& s : d["signals"].toList())
                if (!cat.count(s.toString().toStdString())) missing << s;
            d["missing"] = missing;
            d["view"] = QVariant::fromValue<QObject*>(graphs_->tileView(d["id"].toString(), spec(d)));
        }
        out << d;
    }
    return out;
}

QVariantList PanelBridge::rowsOf(int columns) const {
    const QVariantList ts = tiles();
    QVariantList out;
    for (const auto& r : PanelModel::rows(model_.tiles, columns)) {
        QVariantList row;
        for (int i : r) row << ts[i];
        out << QVariant(row);
    }
    return out;
}

QVariantList PanelBridge::layoutNames() const {
    QVariantList l;
    for (const auto& n : model_.names()) l << n;
    return l;
}

void PanelBridge::setEditing(bool on) {
    if (on != editing_) {
        editing_ = on;
        if (!editing_) setSelected("");
        Q_EMIT editingChanged();
    }
}

void PanelBridge::setSelected(const QString& id_in) {
    QString id = id_in;
    if (!id.isEmpty() && model_.index(id) < 0) id.clear();
    if (id != selected_) {
        selected_ = id;
        Q_EMIT selectedChanged();
    }
}

QString PanelBridge::add(const QString& kind) {
    const QString id = model_.add(kind, graphs_->smoothing());
    tilesEdited();
    return id;
}

void PanelBridge::remove(const QString& id) {
    model_.remove(id);
    if (id == selected_) setSelected("");
    tilesEdited();
}

void PanelBridge::move(const QString& id, int to) {
    model_.move(id, to);
    tilesEdited();
}

void PanelBridge::update(const QString& id, const QVariantMap& changes) {
    model_.update(id, changes);
    tilesEdited();
}

QVariantMap PanelBridge::tile(const QString& id) const {
    const int i = model_.index(id);
    return i >= 0 ? model_.tiles[i].toMap() : QVariantMap{};
}

void PanelBridge::reset() {
    setSelected("");
    model_.reset();
    tilesEdited();
    Q_EMIT layoutsChanged();
}

bool PanelBridge::saveAs(const QString& name) {
    const bool ok = model_.saveAs(name);
    Q_EMIT layoutsChanged();
    return ok;
}

void PanelBridge::load(const QString& name) {
    if (model_.load(name)) {
        setSelected("");
        tilesEdited();
        Q_EMIT layoutsChanged();
    }
}

void PanelBridge::deleteLayout(const QString& name) {
    model_.remove_layout(name);
    Q_EMIT layoutsChanged();
}

QObject* PanelBridge::view(const QString& id) {
    const int i = model_.index(id);
    if (i < 0) return nullptr;
    const QVariantMap t = model_.tiles[i].toMap();
    if (t["type"] != "graph") return nullptr;
    return graphs_->tileView(id, spec(t));
}

void PanelBridge::tilesEdited() {
    std::set<QString> keep;
    for (const auto& v : model_.tiles) {
        const QVariantMap t = v.toMap();
        if (t["type"] == "graph") {
            graphs_->tileView(t["id"].toString(), spec(t));
            keep.insert(t["id"].toString());
        }
    }
    graphs_->dropTiles(keep);
    // queued: delegates are rebuilt back in the event loop, never inside a QML handler
    notify_.start();
    updateValues();
    updateStatus();
}

void PanelBridge::updateStatus() {
    QVariantMap st;
    for (const auto& v : model_.tiles) {
        const QVariantMap t = v.toMap();
        if (t["type"] == "status") st[t["id"].toString()] = statusLines(sim_->session.config, t);
    }
    status_ = st;
    Q_EMIT statusChanged();
}

void PanelBridge::onFrame(double now) {
    if (now >= due_) {
        due_ = now + 1.0 / 15.0;
        updateValues();
    }
}

void PanelBridge::updateValues() {
    const auto& cat = graphs_->catalogue();
    QVariantMap values;
    for (const auto& v : model_.tiles) {
        const QVariantMap t = v.toMap();
        if (t["type"] != "value") continue;
        const auto it = cat.find(t["signal"].toString().toStdString());
        if (it == cat.end()) {
            values[t["id"].toString()] = "—";
            continue;
        }
        const auto& sig = it->second;
        const auto disp = sig.display(t["angle_unit"].toString().toStdString());
        double val;
        bool is_signed = true;
        if (sig.key == "time") {
            val = sim_->simTime();
            is_signed = false;
        } else if (sig.key == "rtf") {
            val = sim_->host().stats().realtime_factor;
            is_signed = false;
        } else if (sig.key == "fps") {
            val = sim_->fps();
            is_signed = false;
        } else {
            const auto col = pendulum::columnIndex(sig.key);
            val = col ? sim_->host().latest(*col) : NAN;
            if (sig.wrap && std::isfinite(val)) val = std::atan2(std::sin(val), std::cos(val));
            val *= disp.scale;
        }
        values[t["id"].toString()] = QString::fromStdString(graph::formatValue(val, disp.decimals, is_signed));
    }
    if (values != values_) {
        values_ = values;
        Q_EMIT valuesChanged();
    }
}

// --------------------------------------------------------------------- Trail
namespace {
constexpr int kMaxTrailPoints = 2400;  // 10 s at 240 fps
constexpr double kMinAlpha = 0.08;
}  // namespace

TrailBridge::TrailBridge(SimulationBridge* sim, QObject* parent) : LineSource(parent), sim_(sim) {
    const QString m = store_.value("quick/view/trail", "tip").toString();
    mode_ = modes().contains(m) ? m : "tip";
    const int s = store_.value("quick/view/trail_s", 3).toInt();
    seconds_ = secondsOptions().contains(s) ? s : 3;
    connect(sim, &SimulationBridge::structureChanged, this, &TrailBridge::clear);
}

void TrailBridge::setMode(const QString& m) {
    if (modes().contains(m) && m != mode_) {
        mode_ = m;
        store_.setValue("quick/view/trail", m);
        Q_EMIT modeChanged();
        draw();
    }
}

void TrailBridge::setSeconds(int s) {
    if (secondsOptions().contains(s) && s != seconds_) {
        seconds_ = s;
        store_.setValue("quick/view/trail_s", s);
        Q_EMIT secondsChanged();
    }
}

void TrailBridge::clear() {
    t_.clear();
    points_.clear();
    draw();
}

void TrailBridge::setMapping(double px, double cx, double oy) {
    if (px != px_ || cx != cx_ || oy != oy_) {
        px_ = px;
        cx_ = cx;
        oy_ = oy;
        draw();
    }
}

void TrailBridge::onFrame(double) {
    if (mode_ == "off") return;
    if (sim_->epoch() != epoch_) {  // reset / new run
        epoch_ = sim_->epoch();
        t_.clear();
        points_.clear();
    }
    const auto& lens = sim_->lengthsVec();
    std::vector<std::pair<double, double>> joints;
    double x = sim_->cartX(), y = 0.0;
    joints.emplace_back(x, y);
    for (std::size_t k = 0; k < lens.size() && k < 3; ++k) {
        x += lens[k] * std::sin(sim_->angles()[k]);
        y += lens[k] * std::cos(sim_->angles()[k]);
        joints.emplace_back(x, y);
    }
    const double t = sim_->simTime();
    if (!t_.empty() && (joints.size() != points_.back().size() || t < t_.back())) {
        t_.clear();
        points_.clear();
    }
    if (!t_.empty() && t == t_.back()) return;  // paused
    std::size_t keep = 0;
    while (keep < t_.size() && t_[keep] < t - seconds_) ++keep;
    if (t_.size() + 1 > kMaxTrailPoints) keep = std::max(keep, t_.size() + 1 - kMaxTrailPoints);
    if (keep) {
        t_.erase(t_.begin(), t_.begin() + static_cast<long>(keep));
        points_.erase(points_.begin(), points_.begin() + static_cast<long>(keep));
    }
    t_.push_back(t);
    points_.push_back(std::move(joints));
    draw();
}

void TrailBridge::draw() {
    curves_.clear();
    const std::size_t n = t_.size();
    if (active_ && has_style_ && mode_ != "off" && n >= 2 && !style_.colors.empty()) {
        const std::size_t jn = points_[0].size();
        std::vector<std::size_t> drawn;
        if (mode_ == "all")
            for (std::size_t j = 1; j < jn; ++j) drawn.push_back(j);
        else
            drawn.push_back(jn - 1);
        graph::Mesh all;
        for (std::size_t j : drawn) {
            std::vector<double> xs(n), ys(n);
            for (std::size_t i = 0; i < n; ++i) {
                xs[i] = cx_ + points_[i][j].first * px_;
                ys[i] = oy_ - points_[i][j].second * px_;
            }
            graph::Mesh m = graph::stroke(xs, ys, style_.width, style_.feather, style_.colors[(j - 1) % style_.colors.size()]);
            for (std::size_t i = 0; i < n; ++i) {  // fade from old to new (premultiplied: every channel)
                const double f = kMinAlpha + (1.0 - kMinAlpha) * (n > 1 ? static_cast<double>(i) / (n - 1) : 1.0);
                for (int k = 0; k < 4; ++k) {
                    auto& v = m.vertices[4 * i + k];
                    v.r = static_cast<std::uint8_t>(v.r * f);
                    v.g = static_cast<std::uint8_t>(v.g * f);
                    v.b = static_cast<std::uint8_t>(v.b * f);
                    v.a = static_cast<std::uint8_t>(v.a * f);
                }
            }
            if (mode_ == "all")
                all.append(m);
            else
                curves_.push_back(std::move(m));
        }
        if (mode_ == "all") curves_.push_back(std::move(all));
    }
    Q_EMIT updated();
}

}  // namespace app
