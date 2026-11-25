#pragma once
// Access to the golden data written by cpp/reference/generate.py (Phase 1).
#include <cmath>
#include <iomanip>
#include <fstream>
#include <limits>
#include <stdexcept>
#include <string>

#include <nlohmann/json.hpp>

namespace reference {

using nlohmann::json;

/// Loads data/<group>.json; throws if the file is missing or of an unknown format.
inline json load(const std::string& group) {
    const std::string path = std::string(PENDULUM_REFERENCE_DIR) + "/" + group + ".json";
    std::ifstream in(path);
    if (!in) throw std::runtime_error("reference data not found: " + path);
    json data = json::parse(in);
    if (data.at("format").get<int>() != 1) throw std::runtime_error("unknown reference format: " + path);
    return data;
}

/// A number from the data; "nan", "inf" and "-inf" are written as strings.
inline double number(const json& v) {
    if (v.is_number()) return v.get<double>();
    const auto s = v.get<std::string>();
    if (s == "nan") return std::numeric_limits<double>::quiet_NaN();
    if (s == "inf") return std::numeric_limits<double>::infinity();
    if (s == "-inf") return -std::numeric_limits<double>::infinity();
    throw std::runtime_error("not a number: " + s);
}

struct Tolerance {
    double rel;
    double abs;
    explicit Tolerance(const json& group) : rel(number(group.at("tolerance").at("rel"))),
                                            abs(number(group.at("tolerance").at("abs"))) {}
    /// |actual − expected| ≤ abs + rel·|expected|; NaN matches NaN.
    bool close(double actual, double expected) const {
        if (std::isnan(expected)) return std::isnan(actual);
        if (std::isinf(expected)) return actual == expected;
        return std::abs(actual - expected) <= abs + rel * std::abs(expected);
    }
};

}  // namespace reference

#include <gtest/gtest.h>
#include <Eigen/Dense>

namespace reference {

inline Eigen::VectorXd vec(const json& v) {
    Eigen::VectorXd out(static_cast<Eigen::Index>(v.size()));
    for (std::size_t i = 0; i < v.size(); ++i) out[static_cast<Eigen::Index>(i)] = number(v[i]);
    return out;
}

inline Eigen::MatrixXd mat(const json& v) {
    const auto r = static_cast<Eigen::Index>(v.size());
    const auto c = r ? static_cast<Eigen::Index>(v[0].size()) : 0;
    Eigen::MatrixXd out(r, c);
    for (Eigen::Index i = 0; i < r; ++i)
        for (Eigen::Index j = 0; j < c; ++j) out(i, j) = number(v[i][j]);
    return out;
}

/// Element-wise comparison with a message naming the first mismatch.
template <typename A, typename B>
::testing::AssertionResult allClose(const A& actual, const B& expected, const Tolerance& tol) {
    if (actual.rows() != expected.rows() || actual.cols() != expected.cols())
        return ::testing::AssertionFailure() << "shape " << actual.rows() << "x" << actual.cols() << " vs "
                                             << expected.rows() << "x" << expected.cols();
    for (Eigen::Index i = 0; i < expected.rows(); ++i)
        for (Eigen::Index j = 0; j < expected.cols(); ++j)
            if (!tol.close(actual(i, j), expected(i, j)))
                return ::testing::AssertionFailure()
                       << "(" << i << "," << j << "): " << std::setprecision(17) << actual(i, j) << " vs "
                       << expected(i, j);
    return ::testing::AssertionSuccess();
}

}  // namespace reference
