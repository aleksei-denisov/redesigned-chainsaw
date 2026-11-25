#pragma once
#include <string_view>

namespace pendulum {

/// Version of the C++ core. The core never depends on Qt.
std::string_view coreVersion() noexcept;

/// Which physics/autotune behaviour the core follows.
/// Classic = main branch, V2 = quick-core-v2 (default on every platform).
enum class CoreMode { Classic, V2 };

constexpr CoreMode kDefaultCoreMode = CoreMode::V2;

}  // namespace pendulum
