"""Database images and owned data volumes must move together, never implicitly."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import compact_database as database
import compact_runtime as runtime
from compact_config import locks


def legacy():
    return {'schema_version': 1, 'series': '4.4', 'volume': database.LEGACY,
            'image': locks()['containers']['images']['mongo']}


def modern():
    return {'schema_version': 1, 'series': '8.0', 'volume': database.PROJECT + '_db8_' + 'a' * 32,
            'image': locks()['compact']['database']['image']}


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name)

    def test_validation_never_pairs_new_image_with_old_volume(self):
        self.assertEqual(database.validate(legacy()), legacy())
        self.assertEqual(database.validate(modern()), modern())
        for value in ({**modern(), 'volume': database.LEGACY},
                      {**legacy(), 'image': modern()['image']},
                      {**modern(), 'image': 'mongo:latest'},
                      {**modern(), 'volume': '/data/db'},
                      {**modern(), 'series': '7.0'}, {**modern(), 'extra': 1}, {}):
            with self.assertRaises(ValueError):
                database.validate(value)

    def test_existing_volume_without_container_is_legacy_not_fresh(self):
        with patch.object(database, 'output', side_effect=[database.LEGACY, '']), \
             patch.object(database, 'inspect_volume'), patch.object(database, 'create_volume') as create:
            selected = database.get(self.state)
        self.assertEqual(selected, legacy())
        create.assert_not_called()
        self.assertEqual(json.loads((self.state / 'database/selection.json').read_text()), selected)

    def test_fresh_preview_does_not_create_or_persist_and_fresh_up_does(self):
        with patch.object(database, 'output', return_value=''), \
             patch.object(database, 'create_volume') as create:
            self.assertEqual(database.get(self.state, create=False)['series'], '8.0')
            create.assert_not_called()
            self.assertFalse((self.state / 'database/selection.json').exists())
            value = database.get(self.state)
            create.assert_called_once_with(value['volume'])

    def test_missing_volume_with_existing_database_container_fails_closed(self):
        with patch.object(database, 'output', side_effect=['', 'existing-container']), \
             patch.object(database, 'create_volume') as create, self.assertRaisesRegex(ValueError, 'empty replacement'):
            database.get(self.state)
        create.assert_not_called()

    def test_selection_loss_or_pending_never_falls_back_to_legacy(self):
        database.atomic(self.state / 'database/selection.json', modern())
        with patch.object(database, 'inspect_volume', side_effect=ValueError('missing')), \
             self.assertRaises(ValueError):
            database.get(self.state)
        database.atomic(self.state / 'database/pending.json', {})
        with patch.object(database, 'output') as out, self.assertRaisesRegex(ValueError, 'unfinished'):
            database.get(self.state)
        out.assert_not_called()

    def test_lost_selection_with_retained_backup_and_new_volume_cannot_downgrade(self):
        with patch.object(database, 'output', return_value=database.LEGACY + '\n' + modern()['volume']), \
             patch.object(database, 'atomic') as save, self.assertRaisesRegex(ValueError, 'never guess legacy'):
            database.get(self.state)
        save.assert_not_called()

    def test_lost_fresh_selection_without_container_cannot_replace_data_with_empty_volume(self):
        with patch.object(database, 'output', return_value=modern()['volume']), \
             patch.object(database, 'create_volume') as create, self.assertRaisesRegex(ValueError, 'never guess'):
            database.get(self.state)
        create.assert_not_called()

    def test_lost_selection_checks_container_even_when_legacy_volume_exists(self):
        item = {'Config': {'Image': modern()['image']},
                'Mounts': [{'Destination': '/data/db', 'Name': 'different'}]}
        with patch.object(database, 'output', side_effect=[database.LEGACY, 'id', json.dumps([item])]), \
             patch.object(database, 'atomic') as save, self.assertRaisesRegex(ValueError, 'legacy fallback'):
            database.get(self.state)
        save.assert_not_called()

    def test_unowned_volume_and_external_consumer_are_rejected(self):
        with patch.object(database, 'output', return_value=json.dumps([
                {'Name': database.LEGACY, 'Driver': 'local', 'Labels': {}, 'Options': {}}])), \
             self.assertRaisesRegex(ValueError, 'unowned'):
            database.inspect_volume(database.LEGACY)
        for labels, running in [({}, False), ({database.LABEL: '1'}, True)]:
            with patch.object(database, 'output', side_effect=['id', json.dumps([
                    {'Config': {'Labels': labels}, 'State': {'Running': running}}])]), \
                 self.assertRaisesRegex(ValueError, 'consumers'):
                database.consumers(modern()['volume'], stopped=True)

    def test_clone_is_cold_readonly_source_and_never_deletes(self):
        with patch.object(database, 'inspect_volume'), patch.object(database, 'consumers') as consumers, \
             patch.object(database, 'create_volume') as create, patch.object(database, 'run') as run:
            database.clone(legacy(), modern())
        consumers.assert_called_once_with(database.LEGACY, stopped=True)
        create.assert_called_once_with(modern()['volume'])
        command = run.call_args.args[0]
        self.assertIn('type=volume,src=' + database.LEGACY + ',dst=/source,readonly', command)
        self.assertEqual(command[-4:], ['cp', '-a', '/source/.', '/destination/'])
        self.assertNotIn('rm', command)

    def test_recovery_preserves_new_writes_after_activation_boundary(self):
        for phase, expected in [('stopping', legacy()), ('upgrading', legacy()), ('activating', modern())]:
            journal = {'schema_version': 1, 'id': 'a' * 32, 'phase': phase,
                       'before': legacy(), 'after': modern()}
            database.atomic(self.state / 'database/pending.json', journal)
            with patch.object(database, 'output', return_value=''), patch.object(database, 'inspect_volume'), \
                 patch.object(database, 'consumers'), patch.object(runtime, 'compose'), patch.object(runtime, 'up') as up:
                database.recover({}, self.state)
            self.assertEqual(json.loads((self.state / 'database/selection.json').read_text()), expected)
            up.assert_called_once_with({}, database_transaction=True)
            self.assertFalse((self.state / 'database/pending.json').exists())

    def test_failed_recovery_retains_journal_and_correct_selection(self):
        database.atomic(self.state / 'database/pending.json', {
            'schema_version': 1, 'id': 'a' * 32, 'phase': 'activating', 'before': legacy(), 'after': modern()})
        with patch.object(database, 'output', return_value=''), patch.object(database, 'inspect_volume'), \
             patch.object(database, 'consumers'), patch.object(runtime, 'compose'), \
             patch.object(runtime, 'up', side_effect=ValueError('unavailable')), \
             self.assertRaises(ValueError):
            database.recover({}, self.state)
        self.assertTrue((self.state / 'database/pending.json').is_file())
        self.assertEqual(json.loads((self.state / 'database/selection.json').read_text()), modern())


if __name__ == '__main__':
    unittest.main()
