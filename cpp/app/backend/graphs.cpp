#include "graphs.h"

#include <algorithm>
#include <chrono>

#include "pendulum/session/rules.hpp"
#include "pendulum/simulation/telemetry.hpp"
#include "simhost.h"
#include "simulation.h"

namespace app {

using namespace graph;

namespace graph {

int columnsFor(double width, double dpr) { return std::max(static_cast<int>(width * std::max(dpr, 1.0) / 2.0), 1); }

GraphState::GraphState(const Spec& spec, const std::map<std::string, Signal>& catalogue, double rate)
    : rate_hz(rate) {
    configure(spec, catalogue);
}

void GraphState::configure(const Spec& spec, const std::map<std::string, Signal>& catalogue) {
    spec_ = spec;
    series_.clear();
    cols_.clear();
    units_.clear();
    axis_of_.clear();
    conv_.clear();
    std::map<int, double> spans;
    for (const auto& key : spec.signals) {
        if (static_cast<int>(series_.size()) >= kMaxSignals) break;
        const auto it = catalogue.find(key);
        const auto col = pendulum::columnIndex(key);
        if (it == catalogue.end() || !it->second.graphable || !col) continue;
        series_.push_back(it->second);
        cols_.push_back(*col);
        const auto d = it->second.display(spec.angle_unit);
        std::string unit = d.unit;
        if (std::find(units_.begin(), units_.end(), unit) == units_.end()) {
            if (static_cast<int>(units_.size()) >= kMaxAxes)
                unit = units_.back();  // a third unit: shares the second axis
            else
                units_.push_back(unit);
        }
        const int a = static_cast<int>(std::find(units_.begin(), units_.end(), unit) - units_.begin());
        axis_of_.push_back(a);
        conv_.push_back(d.scale);
        spans[a] = std::max(spans[a], d.min_span);
    }
    scales_.clear();
    for (std::size_t a = 0; a < units_.size(); ++a) {
        std::optional<std::pair<double, double>> fixed;
        if (a < spec.fixed.size()) fixed = spec.fixed[a];
        scales_.emplace_back(spec.scale, spans.count(static_cast<int>(a)) ? spans[static_cast<int>(a)] : 1.0, fixed);
    }
}

std::vector<int> GraphState::columns() const { return cols_; }

std::vector<LegendEntry> GraphState::legend() const {
    std::vector<LegendEntry> out;
    for (std::size_t k = 0; k < series_.size(); ++k)
        out.push_back({static_cast<int>(k), axis_of_[k], series_[k].key, series_[k].label, units_[axis_of_[k]],
                       series_[k].dashed});
    return out;
}

namespace {
struct Points {
    std::vector<double> t, y, lo, hi;
    std::vector<char> brk;
};

/// Centred moving average over `samples` (odd), shorter at the ends.
void smoothInPlace(std::vector<double>& y, int samples) {
    const int n = static_cast<int>(y.size());
    if (samples <= 1 || n < 3) return;
    const int h = samples / 2;
    std::vector<double> c(n + 1, 0.0);
    for (int i = 0; i < n; ++i) c[i + 1] = c[i] + y[i];
    for (int i = 0; i < n; ++i) {
        const int lo = std::max(i - h, 0), hi = std::min(i + h + 1, n);
        y[i] = (c[hi] - c[lo]) / (hi - lo);
    }
}
}  // namespace

Output GraphState::update(const std::map<int, std::vector<double>>& data, double width, double height, double dpr,
                          double now) {
    Output out;
    const auto tit = data.find(pendulum::kT);
    const bool empty = tit == data.end() || tit->second.empty() || width < 2 || height < 2 || series_.empty();
    if (empty) {
        out.curves.resize(series_.size());
        for (const auto& u : units_) out.axes.push_back({u});
        return out;
    }
    const auto& t = tit->second;
    const double window = spec_.window_s;
    const double col_s = window / columnsFor(width, dpr);
    const double smooth_s = kSmoothingS.count(spec_.smoothing) ? kSmoothingS.at(spec_.smoothing) : 0.0;
    const int smooth_n = smooth_s > 0 ? (static_cast<int>(std::nearbyint(smooth_s * rate_hz)) | 1) : 1;
    const double t_last = t.back();
    const double t0 = t_last - window, t_keep = t0 - col_s;

    std::vector<Points> pts(series_.size());
    for (std::size_t k = 0; k < series_.size(); ++k) {
        const auto cit = data.find(cols_[k]);
        if (cit == data.end()) continue;
        const auto& col = cit->second;
        const bool wrap = series_[k].wrap;
        const std::size_t n = std::min(col.size(), t.size());
        std::size_t i = 0;
        while (i < n) {  // finite runs, also split at wrap-around jumps
            while (i < n && !std::isfinite(col[i])) ++i;
            if (i >= n) break;
            std::vector<double> rt, ry;
            double prev = NAN;
            while (i < n && std::isfinite(col[i])) {
                double v = col[i] * conv_[k];
                if (wrap) v = wrapDegrees(v);
                if (wrap && !std::isnan(prev) && std::abs(v - prev) > 180.0) break;
                rt.push_back(t[i]);
                ry.push_back(v);
                prev = v;
                ++i;
            }
            smoothInPlace(ry, smooth_n);
            bool first = true;
            std::size_t j = 0;
            while (j < rt.size() && rt[j] < t_keep) ++j;
            while (j < rt.size()) {  // envelope per column fixed in time
                const double c = std::floor(rt[j] / col_s);
                double st = 0, sy = 0, lo = ry[j], hi = ry[j];
                int cnt = 0;
                while (j < rt.size() && std::floor(rt[j] / col_s) == c) {
                    st += rt[j];
                    sy += ry[j];
                    lo = std::min(lo, ry[j]);
                    hi = std::max(hi, ry[j]);
                    ++cnt;
                    ++j;
                }
                Points& p = pts[k];
                p.t.push_back(st / cnt);
                p.y.push_back(sy / cnt);
                p.lo.push_back(lo);
                p.hi.push_back(hi);
                p.brk.push_back(first ? 1 : 0);
                first = false;
            }
        }
    }
    // scales from the visible points
    for (std::size_t a = 0; a < scales_.size(); ++a) {
        double dmin = INFINITY, dmax = -INFINITY;
        for (std::size_t k = 0; k < series_.size(); ++k) {
            if (axis_of_[k] != static_cast<int>(a)) continue;
            const Points& p = pts[k];
            for (std::size_t j = 0; j < p.t.size(); ++j)
                if (p.t[j] >= t0) {
                    dmin = std::min(dmin, p.lo[j]);
                    dmax = std::max(dmax, p.hi[j]);
                }
        }
        if (!std::isfinite(dmin)) dmin = dmax = NAN;
        const auto [lo, hi] = scales_[a].update(dmin, dmax, now);
        Axis ax{units_[a], lo, hi, {}};
        const auto [ticks, step] = niceTicks(lo, hi);
        const double sy = hi > lo ? height / (hi - lo) : 0.0;
        for (double v : ticks) ax.ticks.emplace_back((hi - v) * sy, formatTick(v, step));
        out.axes.push_back(std::move(ax));
    }
    const double sx = width / window;
    for (std::size_t k = 0; k < series_.size(); ++k) {
        const Points& p = pts[k];
        const Axis& ax = out.axes[axis_of_[k]];
        const double sy = ax.hi > ax.lo ? height / (ax.hi - ax.lo) : 0.0;
        const std::size_t n = p.t.size();
        std::vector<double> x(n), y(n), ylo(n), yhi(n);
        for (std::size_t j = 0; j < n; ++j) {
            x[j] = (p.t[j] - t_last) * sx + width;
            y[j] = (ax.hi - p.y[j]) * sy;
            ylo[j] = (ax.hi - p.lo[j]) * sy;
            yhi[j] = (ax.hi - p.hi[j]) * sy;
        }
        const Rgba color = style_.colors.empty() ? Rgba{0, 0, 0, 255} : style_.colors[k % style_.colors.size()];
        Mesh mesh = band(x, ylo, yhi, {color[0], color[1], color[2], static_cast<std::uint8_t>(color[3] * kBandAlpha)},
                         2.0 * style_.width, &p.brk, &y);
        mesh.append(stroke(x, y, style_.width, style_.feather, color, series_[k].dashed ? 4.0 * style_.width : 0.0, &p.brk));
        out.curves.push_back(std::move(mesh));
    }
    return out;
}

}  // namespace graph

// ---------------------------------------------------------------- LineSource
void LineSource::setActive(bool on, const void* owner) {
    if (on)
        wanted_.insert(owner);
    else
        wanted_.erase(owner);
    const bool was = active_;
    active_ = !wanted_.empty();
    if (active_ != was) activeChanged();
}

// ----------------------------------------------------------------- GraphView
GraphView::GraphView(const QString& key, const Spec& spec, GraphsBridge* owner, const QString& title)
    : LineSource(owner), state(spec, owner->catalogue(), owner->telemetryRate()), key_(key), title_(title), owner_(owner) {}

QVariantList GraphView::legend() const {
    QVariantList l;
    for (const auto& e : state.legend())
        l << QVariantMap{{"index", e.index},
                         {"key", QString::fromStdString(e.key)},
                         {"label", QString::fromStdString(e.label)},
                         {"axis", e.axis},
                         {"dashed", e.dashed},
                         {"unit", QString::fromStdString(e.unit)}};
    return l;
}

QVariantList GraphView::units() const {
    QVariantList l;
    for (const auto& u : units_) l << QString::fromStdString(u);
    return l;
}

QVariantList GraphView::ranges() const {
    QVariantList l;
    for (const auto& a : axes_) l << QVariant(QVariantList{a.lo, a.hi});
    return l;
}

void GraphView::configure(const Spec& spec) {
    state.rate_hz = owner_->telemetryRate();
    state.configure(spec, owner_->catalogue());
    curves_.clear();
    Q_EMIT legendChanged();
}

void GraphView::apply(Output out) {
    curves_ = std::move(out.curves);
    dx_ = out.dx;
    QVariantList ticks[2];
    std::vector<std::string> units;
    for (std::size_t a = 0; a < out.axes.size() && a < 2; ++a) {
        for (const auto& [y, text] : out.axes[a].ticks) ticks[a] << QVariantMap{{"y", y}, {"text", QString::fromStdString(text)}};
        units.push_back(out.axes[a].unit);
    }
    axes_ = std::move(out.axes);
    if (ticks[0] != ticks_[0] || ticks[1] != ticks_[1] || units != units_) {
        ticks_[0] = ticks[0];
        ticks_[1] = ticks[1];
        units_ = units;
        Q_EMIT axesChanged();
    }
    Q_EMIT updated();
}

// ---------------------------------------------------------------- GraphWorker
GraphWorker::GraphWorker(SimHost* host, Deliver deliver) : host_(host), deliver_(std::move(deliver)) {
    thread_ = std::thread([this] { run(); });
}

GraphWorker::~GraphWorker() { stop(); }

void GraphWorker::stop() {
    {
        std::lock_guard lock(mutex_);
        stop_ = true;
    }
    cv_.notify_one();
    if (thread_.joinable()) thread_.join();
}

void GraphWorker::submit(Job job) {
    {
        std::lock_guard lock(mutex_);
        if (stop_) return;
        pending_ = std::move(job);  // latest wins
    }
    cv_.notify_one();
}

void GraphWorker::run() {
    constexpr long kForgetAfterJobs = 250;  // ~10 s at 25 Hz: states of views no longer shown
    for (;;) {
        Job job;
        {
            std::unique_lock lock(mutex_);
            cv_.wait(lock, [&] { return stop_ || pending_; });
            if (stop_) return;
            job = std::move(*pending_);
            pending_.reset();
        }
        const auto started = std::chrono::steady_clock::now();
        ++jobs_;
        const auto data = host_->tail(job.columns, job.window + 0.5);
        const double now = monotonic();
        std::vector<std::pair<QPointer<GraphView>, Output>> results;
        results.reserve(job.views.size());
        for (auto& v : job.views) {
            Entry& e = states_[v.id];
            if (!e.state)
                e.state = std::make_unique<GraphState>(v.spec, *job.catalogue, job.rate_hz);
            else if (!(e.state->spec() == v.spec) || e.state->rate_hz != job.rate_hz) {
                e.state->rate_hz = job.rate_hz;
                e.state->configure(v.spec, *job.catalogue);
            }
            e.last_used = jobs_;
            e.state->setStyle(v.style);
            try {
                results.emplace_back(v.view, e.state->update(data, v.width, v.height, v.dpr, now));
            } catch (const std::exception&) {
                results.emplace_back(v.view, Output{});
            }
        }
        for (auto it = states_.begin(); it != states_.end();)
            it = jobs_ - it->second.last_used > kForgetAfterJobs ? states_.erase(it) : std::next(it);
        const double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - started).count();
        deliver_(std::move(results), ms);
    }
}

// --------------------------------------------------------------- GraphsBridge
GraphsBridge::~GraphsBridge() { shutdown(); }

void GraphsBridge::shutdown() {
    if (worker_) worker_->stop();
}

GraphsBridge::GraphsBridge(SimulationBridge* sim, QObject* parent) : QObject(parent), sim_(sim) {
    const QVariant stored = settings_.value("quick/graphs/selected");
    QStringList sel = stored.isValid() ? stored.toStringList() : QStringList();
    if (!stored.isValid())
        for (const auto& k : kDefaultSelection) sel << QString::fromStdString(k);
    for (const auto& k : sel)
        if (graphDef(k.toStdString())) selection_.push_back(k.toStdString());
    window_ = settings_.value("quick/graphs/window_s", 10).toInt();
    const std::string sm = settings_.value("quick/graphs/smoothing", "off").toString().toStdString();
    smoothing_ = kSmoothingS.count(sm) ? sm : "off";
    rate_telemetry_ = sim->session.config.simulation.telemetry_rate_hz;
    worker_ = std::make_unique<GraphWorker>(
        &sim->host(), [this](std::vector<std::pair<QPointer<GraphView>, Output>> results, double ms) {
            // back on the GUI thread: swap the finished triangles into the views
            auto shared = std::make_shared<decltype(results)>(std::move(results));
            QMetaObject::invokeMethod(
                this,
                [this, shared, ms] {
                    ms_ = ms;
                    for (auto& [view, out] : *shared)
                        if (view) view->apply(std::move(out));
                },
                Qt::QueuedConnection);
        });
    connect(&timer_, &QTimer::timeout, this, &GraphsBridge::refresh);
    timer_.start(static_cast<int>(1000 / rate_));
    connect(sim, &SimulationBridge::structureChanged, this, &GraphsBridge::onStructure);
    connect(sim, &SimulationBridge::controllerChanged, this, &GraphsBridge::onStructure);
    connect(sim, &SimulationBridge::configChanged, this, [this] {
        if (sim_->session.config.simulation.telemetry_rate_hz != rate_telemetry_) onStructure();
    });
    onStructure();
}

std::vector<std::string> GraphsBridge::labels() const { return pendulum::session::loopLabels(sim_->session.config.controller); }

std::vector<std::string> GraphsBridge::availableKeys() const {
    const auto& p = sim_->session.config.plant;
    std::vector<std::string> out;
    for (const auto& g : graphDefs())
        if (graphAvailable(g.key, p.nSegments(), p.hasCart())) out.push_back(g.key);
    return out;
}

QVariantList GraphsBridge::available() const {
    QVariantList l;
    for (const auto& k : availableKeys()) {
        const auto* g = graphDef(k);
        l << QVariantMap{{"key", QString::fromStdString(k)},
                         {"title", QString::fromStdString(g->title)},
                         {"unit", QString::fromStdString(g->unit)}};
    }
    return l;
}

QVariantList GraphsBridge::selection() const {
    const auto av = availableKeys();
    QVariantList l;
    for (const auto& k : selection_)
        if (std::find(av.begin(), av.end(), k) != av.end()) l << QString::fromStdString(k);
    return l;
}

QVariantList GraphsBridge::windows() const {
    QVariantList l;
    for (int w : kWindowsS) l << w;
    return l;
}

QVariantList GraphsBridge::loopLabels() const {
    QVariantList l;
    for (const auto& s : labels()) l << QString::fromStdString(s);
    return l;
}

QVariantList GraphsBridge::signalList() const {
    QVariantList l;
    // catalogue order (the map is sorted by key): rebuild the display order
    const auto& p = sim_->session.config.plant;
    for (const auto& s : graph::catalogue(p.nSegments(), p.hasCart(), labels())) {
        const auto d = s.display("deg");
        l << QVariantMap{{"key", QString::fromStdString(s.key)},
                         {"label", QString::fromStdString(s.label)},
                         {"name", QString::fromStdString(s.name.empty() ? s.label : s.name)},
                         {"group", QString::fromStdString(s.group)},
                         {"unit", QString::fromStdString(d.unit)},
                         {"graphable", s.graphable},
                         {"angular", s.angular}};
    }
    return l;
}

void GraphsBridge::select(const QString& key, bool on) {
    const std::string k = key.toStdString();
    if (!graphDef(k)) return;
    std::vector<std::string> sel;
    for (const auto& s : selection_)
        if (s != k) sel.push_back(s);
    if (on) {
        sel.push_back(k);
        if (static_cast<int>(sel.size()) > kMaxVisible) sel.erase(sel.begin(), sel.end() - kMaxVisible);
    }
    if (sel != selection_) {
        selection_ = sel;
        QStringList l;
        for (const auto& s : sel) l << QString::fromStdString(s);
        settings_.setValue("quick/graphs/selected", l);
        Q_EMIT selectionChanged();
    }
}

void GraphsBridge::setWindowS(int seconds) {
    if (seconds != window_) {
        window_ = seconds;
        settings_.setValue("quick/graphs/window_s", window_);
        reconfigureCards();
        Q_EMIT windowChanged();
        refresh();
    }
}

void GraphsBridge::setSmoothing(const QString& level) {
    const std::string l = level.toStdString();
    if (kSmoothingS.count(l) && l != smoothing_) {
        smoothing_ = l;
        settings_.setValue("quick/graphs/smoothing", level);
        reconfigureCards();
        Q_EMIT smoothingChanged();
        refresh();
    }
}

void GraphsBridge::setPidLoop(int k) {
    if (k != loop_) {
        loop_ = k;
        reconfigureCards();
        Q_EMIT loopChanged();
    }
}

void GraphsBridge::setRateHz(double hz) {
    hz = std::min(std::max(hz, 5.0), 60.0);
    if (hz != rate_) {
        rate_ = hz;
        timer_.setInterval(static_cast<int>(1000 / hz));
        Q_EMIT rateChanged();
    }
}

void GraphsBridge::onFrame(double now) {
    if (now >= due_) {
        const double interval = 1.0 / rate_;
        due_ += interval;
        if (due_ <= now) due_ = now + interval;
        refresh();
    }
}

Spec GraphsBridge::cardSpec(const std::string& key) const {
    const auto& p = sim_->session.config.plant;
    Spec s;
    s.signals = graphColumns(key, p.nSegments(), labels(), loop_);
    s.window_s = window_;
    s.smoothing = smoothing_;
    return s;
}

QObject* GraphsBridge::view(const QString& key) {
    auto it = views_.find(key);
    if (it == views_.end()) {
        const auto* g = graphDef(key.toStdString());
        it = views_.emplace(key, new GraphView(key, cardSpec(key.toStdString()), this,
                                               g ? QString::fromStdString(g->title) : key)).first;
    }
    return it->second;
}

GraphView* GraphsBridge::tileView(const QString& tile_id, const Spec& spec) {
    auto it = tiles_.find(tile_id);
    if (it == tiles_.end()) return tiles_.emplace(tile_id, new GraphView(tile_id, spec, this, "")).first->second;
    if (!(it->second->state.spec() == spec)) it->second->configure(spec);
    return it->second;
}

void GraphsBridge::dropTiles(const std::set<QString>& keep) {
    for (auto it = tiles_.begin(); it != tiles_.end();) {
        if (keep.count(it->first)) {
            ++it;
            continue;
        }
        GraphView* v = it->second;
        v->setActive(false, nullptr);
        QTimer::singleShot(5000, v, &QObject::deleteLater);  // when the items showing it are surely gone
        it = tiles_.erase(it);
    }
}

void GraphsBridge::reconfigureCards() {
    for (auto& [key, v] : views_)
        if (graphDef(key.toStdString())) v->configure(cardSpec(key.toStdString()));
}

void GraphsBridge::onStructure() {
    const auto& p = sim_->session.config.plant;
    const auto labs = labels();
    loop_ = std::min(loop_, std::max(static_cast<int>(labs.size()) - 1, 0));
    catalogue_.clear();
    for (auto& s : graph::catalogue(p.nSegments(), p.hasCart(), labs)) catalogue_[s.key] = s;
    shared_catalogue_ = std::make_shared<const std::map<std::string, Signal>>(catalogue_);
    rate_telemetry_ = sim_->session.config.simulation.telemetry_rate_hz;
    reconfigureCards();
    for (auto& [id, v] : tiles_) v->configure(v->state.spec());
    Q_EMIT catalogueChanged();
    Q_EMIT availableChanged();
    Q_EMIT selectionChanged();
}

void GraphsBridge::refresh() {
    const auto av = availableKeys();
    std::vector<GraphView*> active;
    for (auto& [k, v] : views_)
        if (v->active() && std::find(av.begin(), av.end(), k.toStdString()) != av.end()) active.push_back(v);
    for (auto& [k, v] : tiles_)
        if (v->active()) active.push_back(v);
    if (active.empty() || !worker_) return;
    const auto started = std::chrono::steady_clock::now();
    GraphWorker::Job job;
    job.window = 0.0;
    job.rate_hz = rate_telemetry_;
    job.catalogue = shared_catalogue_;
    for (auto* v : active) {
        for (int c : v->state.columns())
            if (std::find(job.columns.begin(), job.columns.end(), c) == job.columns.end()) job.columns.push_back(c);
        job.window = std::max(job.window, v->state.spec().window_s);
        job.views.push_back({QPointer<GraphView>(v), v, v->state.spec(), v->style(), v->width(), v->height(), v->dpr()});
    }
    worker_->submit(std::move(job));
    gui_ms_ = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - started).count();
}

}  // namespace app
