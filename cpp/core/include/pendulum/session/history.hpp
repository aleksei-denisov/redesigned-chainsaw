#pragma once
// Undo / redo of parameter edits (session/history.py): snapshots taken before each edit.
// The caller applies a snapshot through the Session like any other edit.
#include <cstddef>
#include <optional>
#include <vector>

namespace pendulum::session {

template <typename T>
class EditHistory {
public:
    explicit EditHistory(std::size_t limit = 100) : limit_(limit) {}

    bool canUndo() const { return !undo_.empty(); }
    bool canRedo() const { return !redo_.empty(); }

    /// The state before an edit; a new edit makes redo impossible.
    void record(const T& before) {
        undo_.push_back(before);
        if (undo_.size() > limit_) undo_.erase(undo_.begin(), undo_.end() - static_cast<std::ptrdiff_t>(limit_));
        redo_.clear();
    }
    std::optional<T> undo(const T& current) {
        if (undo_.empty()) return std::nullopt;
        redo_.push_back(current);
        T s = std::move(undo_.back());
        undo_.pop_back();
        return s;
    }
    std::optional<T> redo(const T& current) {
        if (redo_.empty()) return std::nullopt;
        undo_.push_back(current);
        T s = std::move(redo_.back());
        redo_.pop_back();
        return s;
    }
    void clear() {
        undo_.clear();
        redo_.clear();
    }

private:
    std::size_t limit_;
    std::vector<T> undo_, redo_;
};

}  // namespace pendulum::session
