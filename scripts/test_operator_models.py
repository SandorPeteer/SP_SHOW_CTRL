"""Selection and playback updates must not reset the operator's scroll model."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from operator_models import OperatorRows


class OperatorRowsTests(unittest.TestCase):
    def test_long_list_selection_and_playback_update_preserve_model_identity(self):
        model = OperatorRows()
        rows = [dict(id=str(i), name=f"Cue {i}", selected=i == 0, role="playing" if i == 0 else "")
                for i in range(80)]
        model.set_rows(rows)
        reset, removed, inserted, changed = [], [], [], []
        model.modelReset.connect(lambda: reset.append(True))
        model.rowsRemoved.connect(lambda *args: removed.append(args))
        model.rowsInserted.connect(lambda *args: inserted.append(args))
        model.dataChanged.connect(lambda *args: changed.append(args))
        updated = [dict(row, selected=i == 70, role="next" if i == 70 else row["role"])
                   for i, row in enumerate(rows)]
        model.set_rows(updated)
        self.assertEqual(model.rowCount(), 80)
        self.assertEqual(model.data(model.index(70), model.RowRole)["role"], "next")
        self.assertEqual(model.data(model.index(0), model.RowRole)["role"], "playing")
        self.assertTrue(changed)
        self.assertEqual((reset, removed, inserted), ([], [], []))

    def test_file_add_remove_and_reorder_keep_correct_rows_without_reset(self):
        model = OperatorRows()
        resets = []
        model.modelReset.connect(lambda: resets.append(True))
        for ids in (("a", "b", "c"), ("a", "c"), ("a", "d", "c"), ("c", "d", "a"), ()):
            model.set_rows([dict(id=key, name=key) for key in ids])
            self.assertEqual([model.data(model.index(i), model.RowRole)["id"]
                              for i in range(model.rowCount())], list(ids))
        self.assertEqual(resets, [])


if __name__ == "__main__":
    unittest.main()
