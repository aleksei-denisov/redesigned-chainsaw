# Reference data

Phase 1 adds Python scripts here that run the original `pendulum_sim` package and write
golden JSON files to `data/`. The C++ tests (`cpp/tests`) compare against them, so the
port is proven to give the same results as the Python implementation.
