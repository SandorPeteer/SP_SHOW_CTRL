"""Stable list identities keep browsing independent of playback telemetry."""
from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt


class OperatorRows(QAbstractListModel):
    RowRole = int(Qt.ItemDataRole.UserRole)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []

    def roleNames(self):
        return {self.RowRole: b"modelData"}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == self.RowRole and index.isValid() and 0 <= index.row() < len(self._rows):
            return self._rows[index.row()]
        return None

    def set_rows(self, rows):
        """Change values in place; insert/remove only the changed identity span."""
        old_keys = [row.get("id", index) for index, row in enumerate(self._rows)]
        new_keys = [row.get("id", index) for index, row in enumerate(rows)]
        if old_keys != new_keys:
            prefix = 0
            while prefix < min(len(old_keys), len(new_keys)) and old_keys[prefix] == new_keys[prefix]:
                prefix += 1
            suffix = 0
            while (suffix < min(len(old_keys), len(new_keys)) - prefix
                   and old_keys[-1 - suffix] == new_keys[-1 - suffix]):
                suffix += 1
            removed = len(old_keys) - prefix - suffix
            inserted = len(new_keys) - prefix - suffix
            if removed:
                self.beginRemoveRows(QModelIndex(), prefix, prefix + removed - 1)
                del self._rows[prefix:prefix + removed]
                self.endRemoveRows()
            if inserted:
                self.beginInsertRows(QModelIndex(), prefix, prefix + inserted - 1)
                self._rows[prefix:prefix] = rows[prefix:prefix + inserted]
                self.endInsertRows()
        changed = []
        for index, row in enumerate(rows):
            if self._rows[index] != row:
                self._rows[index] = row
                changed.append(index)
        if changed:
            self.dataChanged.emit(self.index(changed[0]), self.index(changed[-1]), [self.RowRole])
