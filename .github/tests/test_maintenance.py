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

    def discard_profile(self, env):
        # Simulate a fresh profile inside this test's validated temporary home.
        assert env.home == self.home
        for path in env.data.values():
            app.remove_path(path, set(env.data.values()))

    def test_cleanup_combines_browser_and_local_metadata(self):
        self.fingerprint_sample()
        db = self.browser_db()
        app.clean(self.env)
        self.assertNotEqual(app.read_json(self.home / '.claude.json')['userID'], 'old')
        self.assertFalse((self.home / '.claude/backups/.claude.json.backup.old').exists())
        with contextlib.closing(sqlite3.connect(db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM cookies').fetchone()[0], 2)
        self.assertFalse(self.env.backup_root.exists())

    def test_cancelled_cleanup_does_not_change_or_back_up(self):
        self.fingerprint_sample()
        db = self.browser_db()
        before = {key: app.inventory(path) for key, path in self.env.data.items()}
        with patch.object(app, 'confirm', side_effect=app.MaintenanceError('cancelled')):
            with self.assertRaises(app.MaintenanceError):
                app.clean(self.env)
        self.assertEqual(before, {key: app.inventory(path) for key, path in self.env.data.items()})
        with contextlib.closing(sqlite3.connect(db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM cookies').fetchone()[0], 3)
        self.assertFalse(self.env.backup_root.exists())

    def test_cleanup_rechecks_changes_after_confirmation(self):
        self.fingerprint_sample()
        db = self.browser_db()
        def change_file(_):
            app.write_json(self.home / '.claude.json', {'new': True})
        with patch.object(app, 'confirm', side_effect=change_file):
            with self.assertRaises(app.MaintenanceError):
                app.clean(self.env)
        self.assertEqual(app.read_json(self.home / '.claude.json'), {'new': True})
        with contextlib.closing(sqlite3.connect(db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM cookies').fetchone()[0], 3)
        self.assertFalse(self.env.backup_root.exists())

    def test_cleanup_failure_reports_partial_completion_without_backup(self):
        self.fingerprint_sample()
        with patch.object(app, 'apply_browser_plan', side_effect=OSError('database write failed')):
            with self.assertRaises(OSError):
                app.clean(self.env)
        self.assertIn('部分项目可能已经修改', self.stdout._new_target.getvalue())
        self.assertFalse(self.env.backup_root.exists())

    def test_verify_does_not_run_cli_or_modify_data(self):
        self.sample()
        before = {key: app.inventory(path) for key, path in self.env.data.items()}
        with patch.object(app.subprocess, 'run', side_effect=AssertionError('Must not run CLI')):
            app.dispatch(self.env, '5')
        self.assertEqual(before, {key: app.inventory(path) for key, path in self.env.data.items()})
        self.assertIn('验证完成', self.stdout._new_target.getvalue())

    def test_main_menu_is_flat_and_removed_commands_do_not_run(self):
        self.assertEqual(app.ALIASES, {'1': 'backup', '2': 'clean', '3': 'restore', '4': 'sessions', '5': 'verify', '0': 'exit'})
        self.assertNotIn('安装', app.MENU)
        self.assertNotIn('一把梭', app.MENU)
        self.assertNotIn('设备指纹', app.MENU)
        self.assertFalse(hasattr(app, 'install_cli'))
        self.assertFalse(hasattr(app, 'verify_cli'))
        for command in ('install', 'all', 'uninstall', 'fingerprint'):
            with self.assertRaises(app.MaintenanceError):
                app.dispatch(self.env, command)
        self.assertFalse(self.env.backup_root.exists())

    def test_restore_dispatch_recognizes_legacy_fingerprint_backup(self):
        self.fingerprint_sample()
        old = (self.home / '.claude.json').read_bytes()
        root = app.identity_snapshot(self.env, app.identity_plan(self.env))
        app.clean(self.env)
        app.dispatch(self.env, '3', requested=str(root))
        self.assertEqual((self.home / '.claude.json').read_bytes(), old)

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
        self.discard_profile(self.env)
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

    def test_browser_cleans_only_exact_domains_without_backup(self):
        db = self.browser_db()
        app.clean(self.env)
        with contextlib.closing(sqlite3.connect(db)) as conn:
            self.assertEqual(conn.execute('SELECT host_key FROM cookies ORDER BY host_key').fetchall(), [('example.com',), ('notclaude.ai',)])
        self.assertFalse(self.env.backup_root.exists())

    def test_browser_scan_never_writes(self):
        db = self.browser_db()
        original = db.read_bytes()
        app.clean(self.env, scan=True)
        self.assertEqual(db.read_bytes(), original)
        self.assertFalse(self.env.backup_root.exists())

    def test_browser_invalid_database_is_error(self):
        db = self.browser_db()
        db.write_bytes(b'not a sqlite database')
        with self.assertRaises(sqlite3.DatabaseError):
            app.clean(self.env)

    def test_browser_cleanup_handles_wal(self):
        db = self.browser_db()
        with contextlib.closing(sqlite3.connect(db)) as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('INSERT INTO cookies VALUES (?,?)', ('api.anthropic.com', 'WAL-only'))
            conn.commit()
            app.clean(self.env)
            self.assertEqual(conn.execute('SELECT host_key FROM cookies ORDER BY host_key').fetchall(), [('example.com',), ('notclaude.ai',)])
        self.assertFalse(self.env.backup_root.exists())

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
        app.clean(self.env)
        with contextlib.closing(sqlite3.connect(db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM logins').fetchone()[0], 2)

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

    def test_indexeddb_cleans_only_matching_site_without_backup(self):
        root = self.env.browser_roots()['Edge'] / 'Profile 1/IndexedDB'
        target = root / 'https_claude.ai_0.indexeddb.leveldb'
        target.mkdir(parents=True)
        (target / 'data').write_text('site-data')
        other = root / 'https_notclaude.ai_0.indexeddb.leveldb'
        other.mkdir()
        app.clean(self.env)
        self.assertFalse(target.exists())
        self.assertTrue(other.exists())
        self.assertFalse(self.env.backup_root.exists())

    def test_clean_updates_ids_preserves_personal_data_without_backup(self):
        self.fingerprint_sample()
        settings = (self.home / '.claude/settings.json').read_bytes()
        credentials = (self.home / '.claude/.credentials.json').read_bytes()
        chat = (self.home / '.claude/projects/中文-project/session.jsonl').read_bytes()
        with patch.object(app, 'backup', side_effect=AssertionError('Cleanup must not back up')), \
             patch.object(app, 'new_archive', side_effect=AssertionError('Cleanup must not create archives')), \
             patch.object(app, 'identity_snapshot', side_effect=AssertionError('Cleanup must not snapshot')):
            app.dispatch(self.env, '2')
        config = app.read_json(self.home / '.claude.json')
        self.assertNotEqual(config['userID'], 'old')
        self.assertEqual(config['mcpServers'], {'demo': {'command': 'x'}})
        self.assertEqual(config['oauthAccount'], {'token': 'old'})
        self.assertEqual((self.home / '.claude/settings.json').read_bytes(), settings)
        self.assertEqual((self.home / '.claude/.credentials.json').read_bytes(), credentials)
        self.assertEqual((self.home / '.claude/projects/中文-project/session.jsonl').read_bytes(), chat)
        self.assertFalse(self.env.backup_root.exists())

    def fingerprint_sample(self):
        self.sample()
        device = str(__import__('uuid').uuid4())
        self.put('AppData/Roaming/Claude/ant-did', app.base64.b64encode(device.encode()).decode())
        app.write_json(self.env.desktops[0] / 'sentry/scope_v3.json', {'user': {'id': device}, 'installation': 'a' * 32, 'keep': 'unchanged'})
        self.put('.claude/backups/.claude.json.backup.old', '{"userID":"old"}')
        self.put('.claude/backups/other.json', '{"keep":true}')
        self.put('AppData/Local/claude-cli-nodejs/cache-12345678-abcd-1234-5678-123456789abc/data', 'cache')
        self.put('AppData/Local/claude-cli-nodejs/unrelated/data', 'keep')

    def test_fingerprint_full_scope_and_exact_restore(self):
        self.fingerprint_sample()
        before = {key: app.inventory(path) for key, path in self.env.data.items()}
        app.identity_snapshot(self.env, app.identity_plan(self.env))
        app.clean(self.env)
        device = app.base64.b64decode((self.env.desktops[0] / 'ant-did').read_bytes()).decode()
        sentry = app.read_json(self.env.desktops[0] / 'sentry/scope_v3.json')
        self.assertEqual(sentry['user']['id'], device)
        self.assertNotEqual(sentry['installation'], 'a' * 32)
        self.assertEqual(sentry['keep'], 'unchanged')
        self.assertFalse((self.home / '.claude/backups/.claude.json.backup.old').exists())
        self.assertTrue((self.home / '.claude/backups/other.json').exists())
        self.assertFalse((app.identity_cache(self.env) / 'cache-12345678-abcd-1234-5678-123456789abc').exists())
        self.assertTrue((app.identity_cache(self.env) / 'unrelated/data').exists())
        app.restore_identity(self.env, 'latest')
        self.assertEqual(before, {key: app.inventory(path) for key, path in self.env.data.items()})

    def test_fingerprint_scan_preserves_every_file(self):
        self.fingerprint_sample()
        before = {key: app.inventory(path) for key, path in self.env.data.items()}
        app.clean(self.env, scan=True)
        self.assertEqual(before, {key: app.inventory(path) for key, path in self.env.data.items()})
        self.assertFalse(self.env.backup_root.exists())

    def test_fingerprint_keep_history(self):
        self.fingerprint_sample()
        app.clean(self.env, keep_history=True)
        self.assertTrue((self.home / '.claude/backups/.claude.json.backup.old').exists())

    def test_corrupt_fingerprint_backup_prevents_restore(self):
        self.fingerprint_sample()
        app.identity_snapshot(self.env, app.identity_plan(self.env))
        app.clean(self.env)
        root = next(self.env.backup_root.glob('identity-*'))
        (root / 'payload/0').write_text('{}', encoding='utf-8')
        before = (self.home / '.claude.json').read_bytes()
        with self.assertRaises(app.MaintenanceError):
            app.restore_identity(self.env, str(root))
        self.assertEqual(before, (self.home / '.claude.json').read_bytes())

    def test_fingerprint_restore_rejects_unrelated_path(self):
        self.fingerprint_sample()
        app.identity_snapshot(self.env, app.identity_plan(self.env))
        app.clean(self.env)
        root = next(self.env.backup_root.glob('identity-*'))
        data = app.read_json(root / 'identity-manifest.json')
        data['entries'][0]['relative'] = 'Documents/important.txt'
        app.write_json(root / 'identity-manifest.json', data)
        with self.assertRaises(app.MaintenanceError):
            app.restore_identity(self.env, str(root))

    def test_macos_fingerprint_roundtrip(self):
        env = app.Environment(home=self.home, system='Darwin', backup_root=self.home / 'Claude_Backup')
        app.write_json(self.home / '.claude.json', {'userID': 'old', 'mcpServers': {}})
        self.put('Library/Application Support/Claude/ant-did', str(__import__('uuid').uuid4()))
        self.put('Library/Caches/claude-cli-nodejs/cache-12345678-abcd-1234-5678-123456789abc/data', 'cache')
        before = {key: app.inventory(path) for key, path in env.data.items()}
        app.identity_snapshot(env, app.identity_plan(env))
        app.clean(env)
        self.assertNotEqual(app.read_json(self.home / '.claude.json')['userID'], 'old')
        app.restore_identity(env, 'latest')
        self.assertEqual(before, {key: app.inventory(path) for key, path in env.data.items()})

    def test_identity_scan_and_unknown_format_are_read_only(self):
        self.sample()
        before = (self.home / '.claude.json').read_bytes()
        with self.assertRaises(app.MaintenanceError):
            app.clean(self.env)
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
        self.discard_profile(env)
        app.restore(env, backup)
        self.assertEqual(app.read_json(env.desktops[0] / 'claude_desktop_config.json'), {'mcpServers': {'mac': {}}})

    def test_long_paths_roundtrip(self):
        # Modern Python and PyInstaller manifests enable long paths on Windows.
        relative = '.claude/projects/' + '/'.join(['long-directory-' + 'x' * 35] * 6) + '/record.jsonl'
        source = self.put(relative)
        backup = app.backup(self.env)
        self.discard_profile(self.env)
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
