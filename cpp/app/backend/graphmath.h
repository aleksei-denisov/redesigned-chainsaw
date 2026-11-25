#pragma once
// Graph mathematics shared by every live graph (Qt-free):
//   * signal catalogue (quick/backend/signals.py),
//   * nice ticks and graph definitions (mobile/plot_math.py),
//   * stable axis scales (quick/backend/scale.py),
//   * line geometry: envelope decimation, antialiased strokes, bands (quick/backend/linegeom.py).
#include <array>
#include <cmath>
#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace app::graph {

// ---------------------------------------------------------------- signals
struct Signal {
    std::string key, label, group, unit, name;
    bool angular = false, wrap = false;
    double min_span = 1.0;
    bool dashed = false, graphable = true;
    int decimals = 2;
    struct Display {
        double scale;
        std::string unit;
        double min_span;
        int decimals;
    };
    Display display(const std::string& angle_unit = "deg") const;
};
std::vector<Signal> catalogue(int n_segments, bool has_cart, const std::vector<std::string>& loop_labels);
std::string formatValue(double value, int decimals, bool is_signed = true);

// ------------------------------------------------------------- plot maths
double niceStep(double span, int max_ticks);
std::pair<std::vector<double>, double> niceTicks(double lo, double hi, int max_ticks = 5);
std::string formatTick(double value, double step);
double wrapDegrees(double a);

struct GraphDef {
    std::string key, title, unit;
    double min_span;
};
const std::vector<GraphDef>& graphDefs();  ///< angle, angular_velocity, … in display order
const GraphDef* graphDef(const std::string& key);
inline const std::vector<std::string> kDefaultSelection = {"angle", "control"};
inline constexpr int kMaxVisible = 4;
bool graphAvailable(const std::string& key, int n_segments, bool has_cart);
/// Telemetry columns of a Graphs-screen card.
std::vector<std::string> graphColumns(const std::string& key, int n_segments, const std::vector<std::string>& loop_labels,
                                      int loop = 0);

// ------------------------------------------------------------ stable scale
class StableScale {
public:
    StableScale(std::string mode = "auto", double min_span = 1.0,
                std::optional<std::pair<double, double>> fixed = std::nullopt);
    void reset();
    std::pair<double, double> update(double dmin, double dmax, double now);  ///< NaN: no data
    bool moving = false;
    double lo = NAN, hi = NAN;

private:
    std::pair<double, double> rangeFor(double dmin, double dmax) const;
    std::pair<double, double> newTarget(double dmin, double dmax, double now);
    std::string mode_;
    double min_span_;
    std::optional<std::pair<double, double>> fixed_;
    std::pair<double, double> target_{NAN, NAN}, from_{NAN, NAN};
    double ease_start_ = 0.0;
    std::optional<double> small_since_;
};

// ----------------------------------------------------------- line geometry
/// The scene graph's ColoredPoint2D layout: position, premultiplied RGBA.
struct Vertex {
    float x, y;
    std::uint8_t r, g, b, a;
};
static_assert(sizeof(Vertex) == 12);
using Rgba = std::array<std::uint8_t, 4>;

struct Mesh {
    std::vector<Vertex> vertices;
    std::vector<std::uint32_t> indices;
    void append(const Mesh& other);
    bool empty() const { return indices.empty(); }
};

/// Polyline as antialiased triangles: four vertices per point (outer, inner, inner, outer),
/// miter-limited; `breaks[i]` starts a new piece at point i; dash > 0 drops every other dash.
Mesh stroke(const std::vector<double>& x, const std::vector<double>& y, double width, double feather, Rgba rgba,
            double dash = 0.0, const std::vector<char>* breaks = nullptr);
/// Translucent band between lo and hi wherever it is taller than the line already covers.
Mesh band(const std::vector<double>& x, const std::vector<double>& lo, const std::vector<double>& hi, Rgba rgba,
          double min_height, const std::vector<char>* breaks, const std::vector<double>* line);

}  // namespace app::graph
