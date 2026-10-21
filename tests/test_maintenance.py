import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import whack_a_claude as app


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='whack-test-')
        self.home = Path(self.temp.name).resolve() / '中文 user with spaces'
        self.home.mkdir()
        self.env = app.Environment(home=self.home, system='Windows',
                                   local=self.home / 'AppData/Local', roaming=self.home / 'AppData/Roaming',
                                   backup_root=self.home / 'Claude_Backup')
        self.closed = patch.object(app, 'require_closed')
        self.closed.start()
        self.confirmed = patch.object(app, 'confirm')
        self.confirmed.start()
        self.stdout = contextlib.redirect_stdout(io.StringIO())
        self.stdout.__enter__()
        self.addCleanup(self.cleanup_temp)
        self.addCleanup(self.closed.stop)
        self.addCleanup(self.confirmed.stop)
        self.addCleanup(self.stdout.__exit__, None, None, None)

    def cleanup_temp(self):
        target = Path(self.temp.name).resolve()
        # Test cleanup has its own validated boundary, including long paths.
        assert target.parent == Path(tempfile.gettempdir()).resolve()
        assert target.name.startswith('whack-test-')
        shutil.rmtree(app.fs_path(target))
        self.temp.cleanup()

    def put(self, rel, content='测试内容'):
        target = app.fs_path(self.home / rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
        return target

    def sample(self):
        self.put('.claude/skills/custom/SKILL.md')
        self.put('.claude/projects/中文-project/session.jsonl', '{}\n')
        self.put('.claude/settings.json', '{"env":{"MY_SETTING":"value"}}')
        app.write_json(self.home / '.claude.json', {'oauthAccount': {'token': 'old'}, 'userID': 'old',
                       'machineID': 'old', 'mcpServers': {'demo': {'command': 'x'}}, 'projects': {}})
        app.write_json(self.home / '.claude/.credentials.json', {'claudeAiOauth': {'token': 'old'},
                       'oauthAccount': 'old', 'mcpOAuth': {'demo': 'keep'}})
        app.write_json(self.env.desktops[0] / 'claude_desktop_config.json', {'mcpServers': {'test': {}}})
        self.put('AppData/Roaming/Claude/ant-did', 'original-device')

    def test_full_backup_hash_and_noncolliding_names(self):
        self.sample()
        first = app.backup(self.env)
        second = app.backup(self.env)
        self.assertNotEqual(first, second)
        manifest = app.validate_backup(self.env, first, fresh=True)
        self.assertIn('claude', manifest['entries'])
        self.assertIn('data-0', manifest['entries'])
        self.assertEqual((first / 'payload/claude/skills/custom/SKILL.md').read_text(encoding='utf-8'), '测试内容')

    def test_empty_profile_refuses_completed_backup(self):
        with self.assertRaises(app.MaintenanceError):
            app.backup(self.env)
        self.assertFalse(list(self.env.backup_root.glob('*/manifest.json')))

    def test_partial_copy_never_marks_complete(self):
        self.sample()
        with patch.object(app, 'copy_path', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                app.backup(self.env)
        self.assertFalse(list(self.env.backup_root.glob('*/manifest.json')))

    def test_corrupt_backup_prevents_deletion(self):
        self.sample()
        root = app.backup(self.env)
        (root / 'payload/claude/settings.json').write_text('changed', encoding='utf-8')
        with self.assertRaises(app.MaintenanceError):
            app.reset_data(self.env, root)
        self.assertTrue((self.home / '.claude.json').exists())

    def test_stale_backup_prevents_deletion(self):
        self.sample()
        root = app.backup(self.env)
        self.put('.claude/projects/中文-project/new.jsonl')
        with self.assertRaises(app.MaintenanceError):
            app.reset_data(self.env, root)
        self.assertTrue((self.home / '.claude.json').exists())

    def test_new_data_root_prevents_deletion(self):
        self.put('.claude/settings.json', '{}')
        root = app.backup(self.env)
        self.put('AppData/Local/Claude/new')
        with self.assertRaises(app.MaintenanceError):
            app.reset_data(self.env, root)

    def test_change_during_confirmation_prevents_deletion(self):
        self.sample()
        root = app.backup(self.env)
        with patch.object(app, 'confirm', side_effect=lambda _: self.put('.claude/new-data')):
            with self.assertRaises(app.MaintenanceError):
                app.reset_data(self.env, root)
        self.assertTrue((self.home / '.claude/new-data').exists())

    def test_backup_inside_data_refused(self):
        with self.assertRaises(app.MaintenanceError):
            app.Environment(home=self.home, system='Darwin', backup_root=self.home / '.claude/backup')

    def test_delete_target_cannot_escape_allowlist(self):
        path = self.put('unrelated')
        with self.assertRaises(app.MaintenanceError):
            app.remove_path(path, set(self.env.data.values()))
        self.assertTrue(path.exists())

    def test_roundtrip_preserves_personal_data_sanitizes_account(self):
        self.sample()
        root = app.backup(self.env)
        app.reset_data(self.env, root)
        self.assertFalse((self.home / '.claude').exists())
        app.restore(self.env, root)
        config = app.read_json(self.home / '.claude.json')
        self.assertFalse(app.IDENTITY & config.keys())
        self.assertIn('mcpServers', config)
        creds = app.read_json(self.home / '.claude/.credentials.json')
        self.assertEqual(creds, {'mcpOAuth': {'demo': 'keep'}})
        self.assertTrue((self.home / '.claude/skills/custom/SKILL.md').exists())
        self.assertTrue((self.env.desktops[0] / 'claude_desktop_config.json').exists())
        self.assertFalse((self.env.desktops[0] / 'ant-did').exists())

    def test_restore_snapshots_current_data_and_keeps_extra_files(self):
        self.sample()
        root = app.backup(self.env)
        self.put('.claude/settings.json', '{"new":true}')
        self.put('.claude/new-file', 'keep')
        app.restore(self.env, root)
        snapshots = list(self.env.backup_root.glob('before-restore-*'))
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(app.read_json(snapshots[0] / 'payload/claude/settings.json'), {'new': True})
        self.assertTrue((self.home / '.claude/new-file').exists())

    def test_malformed_json_refuses_restore_before_writes(self):
        self.sample()
        self.put('.claude.json', '{invalid')
        root = app.backup(self.env)
        self.put('.claude/settings.json', '{"current":true}')
        with self.assertRaises(ValueError):
            app.restore(self.env, root)
        self.assertEqual(app.read_json(self.home / '.claude/settings.json'), {'current': True})
        self.assertFalse(list(self.env.backup_root.glob('before-restore-*')))

    def test_cross_os_backup_rejected(self):
        self.sample()
        root = app.backup(self.env)
        manifest = app.read_json(root / 'manifest.json')
        manifest['system'] = 'Darwin'
        app.write_json(root / 'manifest.json', manifest)
        with self.assertRaises(app.MaintenanceError):
            app.validate_backup(self.env, root)

    def test_malicious_manifest_key_rejected(self):
        self.sample()
        root = app.backup(self.env)
        manifest = app.read_json(root / 'manifest.json')
        manifest['entries']['../../outside'] = {'files': {}}
        app.write_json(root / 'manifest.json', manifest)
        with self.assertRaises(app.MaintenanceError):
            app.validate_backup(self.env, root)

    def test_domains_are_exact_or_subdomains(self):
        for value in ('.claude.ai', 'api.anthropic.com', 'https://claude.com/path', 'HTTPS://CLAUDE.AI/test'):
            self.assertTrue(app.domain_match(value), value)
        for value in ('notclaude.ai', 'claude.ai.evil.test', 'https://example.org/claude.ai', 'anthropic-example.com'):
            self.assertFalse(app.domain_match(value), value)

    def test_idb_names_are_exact(self):
        self.assertTrue(app.idb_match('https_claude.ai_0.indexeddb.leveldb'))
        self.assertTrue(app.idb_match('https_api.anthropic.com_0.indexeddb.blob'))
        self.assertFalse(app.idb_match('https_notclaude.ai_0.indexeddb.leveldb'))
        self.assertFalse(app.idb_match('random-claude-data'))

    def browser_db(self):
        db = self.env.browser_roots()['Chrome'] / 'Default/Network/Cookies'
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db)
        conn.execute('CREATE TABLE cookies(host_key TEXT, value TEXT)')
        conn.executemany('INSERT INTO cookies VALUES (?,?)', [('.claude.ai', 'a'), ('example.com', 'b'), ('notclaude.ai', 'c')])
        conn.commit()
        conn.close()
        return db

    def test_browser_cleans_only_exact_domains_and_keeps_backup(self):
        db = self.browser_db()
        app.clean_browser(self.env)
        with contextlib.closing(sqlite3.connect(db)) as conn:
            self.assertEqual(conn.execute('SELECT host_key FROM cookies ORDER BY host_key').fetchall(), [('example.com',), ('notclaude.ai',)])
        root = next(self.env.backup_root.glob('browser-*'))
        with contextlib.closing(sqlite3.connect(root / '0')) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM cookies').fetchone()[0], 3)

    def test_browser_scan_never_writes(self):
        db = self.browser_db()
        original = db.read_bytes()
        app.clean_browser(self.env, scan=True)
        self.assertEqual(db.read_bytes(), original)
        self.assertFalse(self.env.backup_root.exists())

    def test_browser_backup_failure_prevents_delete(self):
        db = self.browser_db()
        with patch.object(app, 'write_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                app.clean_browser(self.env)
        with contextlib.closing(sqlite3.connect(db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM cookies').fetchone()[0], 3)

    def test_browser_invalid_database_is_error(self):
        db = self.browser_db()
        db.write_bytes(b'not a sqlite database')
        with self.assertRaises(sqlite3.DatabaseError):
            app.clean_browser(self.env)

    def test_browser_sqlite_backup_includes_wal(self):
        db = self.browser_db()
        with contextlib.closing(sqlite3.connect(db)) as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('INSERT INTO cookies VALUES (?,?)', ('api.anthropic.com', 'WAL-only'))
            conn.commit()
            app.clean_browser(self.env)
            root = next(self.env.backup_root.glob('browser-*'))
            with contextlib.closing(sqlite3.connect(root / '0')) as snapshot:
                self.assertEqual(snapshot.execute('SELECT COUNT(*) FROM cookies').fetchone()[0], 4)

    def test_saved_login_exact_domain_matching(self):
        db = self.env.browser_roots()['Chrome'] / 'Default/Login Data'
        db.parent.mkdir(parents=True)
        with contextlib.closing(sqlite3.connect(db)) as conn:
            conn.execute('CREATE TABLE logins(origin_url TEXT, signon_realm TEXT)')
            conn.executemany('INSERT INTO logins VALUES (?,?)', [
                ('https://claude.ai/login', 'https://claude.ai'),
                ('https://other.test/claude.ai', 'https://other.test'),
                ('https://notclaude.ai', 'https://notclaude.ai')])
            conn.commit()
        app.clean_browser(self.env)
        with contextlib.closing(sqlite3.connect(db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM logins').fetchone()[0], 2)

    def test_atomic_identity_failure_rolls_back_all_files(self):
        self.sample()
        self.put('AppData/Roaming/Claude/ant-did', str(__import__('uuid').uuid4()))
        config = (self.home / '.claude.json').read_bytes()
        device = (self.env.desktops[0] / 'ant-did').read_bytes()
        original = app.atomic_bytes
        calls = 0
        def fail_second_change(path, content):
            nonlocal calls
            # Manifest writing uses this same function; fail on device write only once.
            if Path(path).name == 'ant-did' and calls == 0:
                calls += 1
                raise OSError('simulated write error')
            return original(path, content)
        with patch.object(app, 'atomic_bytes', side_effect=fail_second_change):
            with self.assertRaises(OSError):
                app.rotate_identity(self.env)
        self.assertEqual((self.home / '.claude.json').read_bytes(), config)
        self.assertEqual((self.env.desktops[0] / 'ant-did').read_bytes(), device)

    def test_windows_node_cli_blocks_operations(self):
        with patch.object(app.subprocess, 'run', side_effect=[
                subprocess.CompletedProcess([], 0, b'"node.exe","100","Console","1","100 K"\n'),
                subprocess.CompletedProcess([], 0, b'1\r\n')]):
            self.assertEqual(app.running(self.env), ['node (Claude Code)'])

    def test_concurrent_lock_is_refused_and_released(self):
        with app.exclusive(self.env):
            with self.assertRaises(app.MaintenanceError):
                with app.exclusive(self.env):
                    pass
        with app.exclusive(self.env):
            pass

    def test_indexeddb_backed_up_before_deletion(self):
        root = self.env.browser_roots()['Edge'] / 'Profile 1/IndexedDB'
        target = root / 'https_claude.ai_0.indexeddb.leveldb'
        target.mkdir(parents=True)
        (target / 'data').write_text('keep-backup')
        other = root / 'https_notclaude.ai_0.indexeddb.leveldb'
        other.mkdir()
        app.clean_browser(self.env)
        self.assertFalse(target.exists())
        self.assertTrue(other.exists())
        archive = next(self.env.backup_root.glob('browser-*'))
        self.assertEqual((archive / '0/data').read_text(), 'keep-backup')

    def test_failed_install_in_combination_restores_data(self):
        self.sample()
        with patch.object(app, 'install_cli', side_effect=app.MaintenanceError('download failed')):
            with self.assertRaises(app.MaintenanceError):
                app.dispatch(self.env, 'all')
        self.assertTrue((self.home / '.claude/skills/custom/SKILL.md').exists())
        self.assertFalse(app.IDENTITY & app.read_json(self.home / '.claude.json').keys())

    def test_malformed_config_combination_stops_before_delete(self):
        self.sample()
        self.put('.claude.json', 'broken json')
        with self.assertRaises(ValueError):
            app.dispatch(self.env, 'all')
        self.assertTrue((self.home / '.claude/skills/custom/SKILL.md').exists())

    def test_identity_changes_only_existing_ids_and_keeps_format(self):
        self.sample()
        device = str(__import__('uuid').uuid4())
        self.put('AppData/Roaming/Claude/ant-did', app.base64.b64encode(device.encode()).decode())
        app.rotate_identity(self.env)
        config = app.read_json(self.home / '.claude.json')
        self.assertNotEqual(config['userID'], 'old')
        self.assertEqual(config['mcpServers'], {'demo': {'command': 'x'}})
        self.assertEqual(config['oauthAccount'], {'token': 'old'})
        self.assertNotEqual(app.base64.b64decode((self.env.desktops[0] / 'ant-did').read_bytes()).decode(), device)
        root = next(self.env.backup_root.glob('backup-*'))
        self.assertEqual(app.read_json(root / 'payload/config')['userID'], 'old')

    def test_identity_scan_and_unknown_format_are_read_only(self):
        self.sample()
        before = (self.home / '.claude.json').read_bytes()
        with self.assertRaises(app.MaintenanceError):
            app.rotate_identity(self.env)
        self.assertEqual(before, (self.home / '.claude.json').read_bytes())
        self.assertFalse(self.env.backup_root.exists())

    def test_sessions_use_backup_template_and_are_idempotent(self):
        self.sample()
        desktop = self.env.desktops[0]
        target = desktop / 'claude-code-sessions/new-account/new-org'
        target.mkdir(parents=True)
        old = desktop / 'claude-code-sessions/old-account/old-org/local_old.json'
        app.write_json(old, {'cliSessionId': 'old', 'model': 'template-model', 'oauthToken': 'DO-NOT-COPY', 'alwaysAllowedReasons': ['unsafe']})
        self.put('.claude/projects/中文-project/session.jsonl', '\n'.join(json.dumps(row) for row in [
            {'type': 'user', 'cwd': '/original', 'timestamp': '2026-01-01T00:00:00Z', 'message': {'content': 'hello'}},
            {'type': 'assistant', 'cwd': '/original/subdir', 'timestamp': '2026-01-02T00:00:00Z', 'message': {'model': 'actual-model'}}]))
        backup = app.backup(self.env)
        old.unlink()
        app.rebuild_sessions(self.env, backup, str(target))
        made = list(target.glob('local_*.json'))
        self.assertEqual(len(made), 1)
        row = app.read_json(made[0])
        self.assertEqual(row['cwd'], '/original')
        self.assertEqual(row['model'], 'actual-model')
        self.assertNotIn('oauthToken', row)
        self.assertNotIn('alwaysAllowedReasons', row)
        app.rebuild_sessions(self.env, backup, str(target))
        self.assertEqual(len(list(target.glob('local_*.json'))), 1)

    def test_sessions_refuse_external_target(self):
        self.sample()
        backup = app.backup(self.env)
        with self.assertRaises(app.MaintenanceError):
            app.rebuild_sessions(self.env, backup, str(self.home))

    def test_macos_paths_and_desktop_restore(self):
        env = app.Environment(home=self.home, system='Darwin', backup_root=self.home / 'Claude_Backup')
        self.put('.claude/settings.json', '{}')
        app.write_json(env.desktops[0] / 'claude_desktop_config.json', {'mcpServers': {'mac': {}}})
        backup = app.backup(env)
        app.reset_data(env, backup)
        app.restore(env, backup)
        self.assertEqual(app.read_json(env.desktops[0] / 'claude_desktop_config.json'), {'mcpServers': {'mac': {}}})

    def test_long_paths_roundtrip(self):
        # Modern Python and PyInstaller manifests enable long paths on Windows.
        relative = '.claude/projects/' + '/'.join(['long-directory-' + 'x' * 35] * 6) + '/record.jsonl'
        source = self.put(relative)
        backup = app.backup(self.env)
        app.reset_data(self.env, backup)
        app.restore(self.env, backup)
        self.assertEqual(source.read_text(encoding='utf-8'), '测试内容')

    def test_linked_data_refused(self):
        self.put('outside/data')
        link = self.home / '.claude'
        try:
            link.symlink_to(self.home / 'outside', target_is_directory=True)
        except OSError:
            if os.name == 'nt':
                result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(self.home / 'outside')], capture_output=True)
                if result.returncode:
                    self.skipTest('Symlink/junction privilege unavailable')
            else:
                raise
        with self.assertRaises(app.MaintenanceError):
            app.backup(self.env)
        self.assertTrue((self.home / 'outside/data').exists())

    def test_runtime_rejects_running_process(self):
        self.closed.stop()
        with patch.object(app, 'running', return_value=['claude.exe']):
            with self.assertRaises(app.MaintenanceError):
                app.backup(self.env)

    def test_manifest_incomplete_not_selected_as_latest(self):
        self.sample()
        valid = app.backup(self.env)
        incomplete = self.env.backup_root / 'backup-99999999'
        incomplete.mkdir()
        self.assertEqual(app.choose_backup(self.env, 'latest'), valid)

    def test_cancel_backup_selection_does_not_fall_back(self):
        self.sample()
        app.backup(self.env)
        with patch('builtins.input', return_value=''):
            with self.assertRaises(app.MaintenanceError):
                app.choose_backup(self.env)


if __name__ == '__main__':
    unittest.main()
