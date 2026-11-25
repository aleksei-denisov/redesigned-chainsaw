#pragma once
// Seedable random generator used everywhere in the core (sensor noise, auto-tune sampling):
// xoshiro256** seeded through SplitMix64, normal deviates by the Marsaglia polar method.
// Pure integer/IEEE arithmetic: the same seed gives the same numbers on every platform.
#include <cmath>
#include <cstdint>

namespace pendulum {

class Rng {
public:
    explicit Rng(std::uint64_t seed = 0) { reseed(seed); }

    void reseed(std::uint64_t seed) {
        std::uint64_t x = seed;
        for (auto& w : s_) w = splitmix64(x);
        has_spare_ = false;
    }

    std::uint64_t next() {
        const std::uint64_t result = rotl(s_[1] * 5, 7) * 9;
        const std::uint64_t t = s_[1] << 17;
        s_[2] ^= s_[0];
        s_[3] ^= s_[1];
        s_[1] ^= s_[2];
        s_[0] ^= s_[3];
        s_[2] ^= t;
        s_[3] = rotl(s_[3], 45);
        return result;
    }

    /// Uniform in [0, 1) with 53 random bits.
    double uniform() { return static_cast<double>(next() >> 11) * 0x1.0p-53; }

    /// Normal deviate with the given mean and standard deviation.
    double normal(double mean = 0.0, double stddev = 1.0) {
        if (has_spare_) {
            has_spare_ = false;
            return mean + stddev * spare_;
        }
        double u, v, s;
        do {
            u = 2.0 * uniform() - 1.0;
            v = 2.0 * uniform() - 1.0;
            s = u * u + v * v;
        } while (s >= 1.0 || s == 0.0);
        const double f = std::sqrt(-2.0 * std::log(s) / s);
        spare_ = v * f;
        has_spare_ = true;
        return mean + stddev * (u * f);
    }

private:
    static std::uint64_t rotl(std::uint64_t x, int k) { return (x << k) | (x >> (64 - k)); }
    static std::uint64_t splitmix64(std::uint64_t& x) {
        std::uint64_t z = (x += 0x9e3779b97f4a7c15ULL);
        z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
        z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
        return z ^ (z >> 31);
    }
    std::uint64_t s_[4]{};
    double spare_ = 0.0;
    bool has_spare_ = false;
};

}  // namespace pendulum
