import runtime_config
"""Account management attached to the gateway's private SQLite database."""
import hashlib
import hmac
import json
import re
import secrets

ITERATIONS = 600_000
THEMES = {'classic', 'journal'}
PAGES = {'today', 'todos', 'finance', 'work', 'notes', 'sport', 'health', 'travel', 'treasures', 'games', 'weather', 'people', 'recipes'}


def password_record(value):
    if not isinstance(value, str) or len(value) < 14 or len(value.encode()) > 1024:
        raise ValueError('密码至少 14 个字符，最长 1024 字节')
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', value.encode(), salt, ITERATIONS)
    return dict(algorithm='pbkdf2-sha256', iterations=ITERATIONS, salt=salt.hex(), hash=digest.hex())


def matches(value, record):
    if not isinstance(value, str) or len(value.encode()) > 1024 or not record:
        return False
    digest = hashlib.pbkdf2_hmac('sha256', value.encode(), bytes.fromhex(record['salt']), ITERATIONS)
    return hmac.compare_digest(digest.hex(), record['hash'])


def public(row):
    return {k: row[k] for k in ('id', 'username', 'display_name', 'role', 'status', 'created')}


class Accounts:
    def __init__(self, state):
        self.state = state
        with state.connect() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS users (
                  id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                  display_name TEXT NOT NULL, role TEXT NOT NULL, status TEXT NOT NULL,
                  password TEXT, generation TEXT NOT NULL, settings TEXT NOT NULL DEFAULT '{}',
                  created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS invitations (
                  digest TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires REAL NOT NULL, used REAL);
                CREATE TABLE IF NOT EXISTS account_events (
                  id INTEGER PRIMARY KEY, actor TEXT NOT NULL, target TEXT NOT NULL,
                  action TEXT NOT NULL, at REAL NOT NULL);
            ''')
            c.execute('BEGIN IMMEDIATE')
            cols = {r['name'] for r in c.execute('PRAGMA table_info(sessions)')}
            if 'user_id' not in cols:
                c.execute("ALTER TABLE sessions ADD COLUMN user_id TEXT NOT NULL DEFAULT 'owner'")
            for uid, name, role in [('owner', runtime_config.ADMIN_USERNAME, 'admin'), ('demo', 'demo', 'demo')]:
                c.execute('INSERT OR IGNORE INTO users (id,username,display_name,role,status,generation,created) VALUES (?,?,?,?,?,?,?)',
                          (uid, name, '体验空间' if uid == 'demo' else name, role, 'active', secrets.token_hex(16), state.clock()))

    def record(self, uid, data=None):
        with self.state.connect() as c:
            row = c.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        if not row:
            return None
        row = dict(row)
        row['settings'] = json.loads(row['settings'])
        row['password'] = json.loads(row['password']) if row['password'] else None
        if uid == 'owner':
            config = data if data is not None else self.state.config()
            row['password'] = config.get('password')
            row['generation'] = config.get('generation')
        return row

    def identity(self, uid, data=None):
        row = self.record(uid, data)
        if not row or row['status'] != 'active':
            return None
        return {**public(row), 'settings': row['settings']}

    def authenticate(self, username, password, data):
        uid = None
        if isinstance(username, str) and len(username) <= 64:
            with self.state.connect() as c:
                row = c.execute('SELECT id FROM users WHERE username=?', (username.strip(),)).fetchone()
                uid = row['id'] if row else None
        row = self.record(uid, data) if uid else None
        # Unknown usernames do the same expensive hash work; never expose existence.
        record = row['password'] if row and row['password'] else data.get('password')
        valid = matches(password, record)
        return row if row and row['role'] not in ('demo', 'guest') and row['status'] == 'active' and valid else None

    def list(self):
        with self.state.connect() as c:
            return [public(r) for r in c.execute("SELECT * FROM users WHERE role != 'guest' ORDER BY created,id")]

    def create_guest(self):
        """A new, passwordless sandbox; never reuse an owner's or visitor's store."""
        import guest_seed
        import tenant_storage
        import shutil
        uid = 'u' + secrets.token_hex(16)
        # Guests live for at most a 12-hour session. Remove only old guest rows
        # and their exact tenant directories, never member/owner directories.
        with self.state.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            expired = [r['id'] for r in c.execute(
                "SELECT id FROM users WHERE role='guest' AND created < ?",
                (self.state.clock() - 2 * 86400,))]
            for old in expired:
                if not re.fullmatch(r'u[0-9a-f]{32}', old):
                    continue
                with tenant_storage.scope(old):
                    folder = tenant_storage.root()
                    if folder.exists():
                        shutil.rmtree(folder)
                c.execute('DELETE FROM sessions WHERE user_id=?', (old,))
                c.execute("DELETE FROM users WHERE id=? AND role='guest'", (old,))
        seeded = False
        try:
            guest_seed.seed(uid)
            seeded = True
            with self.state.connect() as c:
                c.execute('INSERT INTO users (id,username,display_name,role,status,generation,created,settings) VALUES (?,?,?,?,?,?,?,?)',
                          (uid, 'guest-' + uid[1:], '游客演示', 'guest', 'active',
                           secrets.token_hex(16), self.state.clock(),
                           json.dumps({'theme':'classic','last_page':'today','weather_city':{'name':'杭州','latitude':30.2741,'longitude':120.1551}})))
        except Exception:
            with tenant_storage.scope(uid):
                folder = tenant_storage.root()
                if seeded and folder.exists():
                    shutil.rmtree(folder)
            raise
        return uid

    def event(self, c, actor, target, action):
        c.execute('INSERT INTO account_events (actor,target,action,at) VALUES (?,?,?,?)',
                  (actor, target, action, self.state.clock()))

    def issue(self, payload):
        action = payload.get('action')
        token = secrets.token_urlsafe(32)
        expiry = self.state.clock() + 86400
        with self.state.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if action == 'create':
                username = payload.get('username', '')
                name = payload.get('display_name', '')
                if not isinstance(username, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{2,39}', username):
                    raise ValueError('用户名请用 3—40 位字母、数字、点、横线或下划线')
                if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
                    raise ValueError('请填写 1—40 字的称呼')
                if c.execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone():
                    raise ValueError('此用户名已被使用')
                uid = 'u' + secrets.token_hex(16)
                c.execute('INSERT INTO users (id,username,display_name,role,status,generation,created) VALUES (?,?,?,?,?,?,?)',
                          (uid, username, name.strip(), 'member', 'invited', secrets.token_hex(16), self.state.clock()))
            elif action == 'invite':
                uid = payload.get('id')
                row = c.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
                if not row or row['role'] != 'member':
                    raise ValueError('请选择普通账号')
                c.execute("UPDATE users SET status='invited',generation=? WHERE id=?", (secrets.token_hex(16), uid))
                c.execute('DELETE FROM sessions WHERE user_id=?', (uid,))
            else:
                raise ValueError('无效的邀请操作')
            c.execute('DELETE FROM invitations WHERE user_id=? OR expires<?', (uid, self.state.clock()))
            c.execute('INSERT INTO invitations (digest,user_id,expires) VALUES (?,?,?)',
                      (self.state.digest(token), uid, expiry))
            self.event(c, 'owner', uid, action)
        return dict(user=self.identity_or_invited(uid), invitation=token, expires=expiry)

    def identity_or_invited(self, uid):
        return public(self.record(uid))

    def invitation(self, token):
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            raise ValueError('邀请已失效，请联系管理员重新邀请')
        with self.state.connect() as c:
            row = c.execute("SELECT u.*,i.expires FROM invitations i JOIN users u ON u.id=i.user_id WHERE i.digest=? AND i.used IS NULL AND i.expires>? AND u.status='invited'",
                            (self.state.digest(token), self.state.clock())).fetchone()
        if not row:
            raise ValueError('邀请已失效，请联系管理员重新邀请')
        return {**public(row), 'expires': row['expires']}

    def activate(self, token, password):
        target = self.invitation(token)
        hashed = password_record(password)
        with self.state.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute("SELECT i.user_id FROM invitations i JOIN users u ON u.id=i.user_id WHERE i.digest=? AND i.used IS NULL AND i.expires>? AND u.status='invited'",
                            (self.state.digest(token), self.state.clock())).fetchone()
            if not row or row['user_id'] != target['id']:
                raise ValueError('邀请已失效，请联系管理员重新邀请')
            c.execute("UPDATE users SET password=?,generation=?,status='active' WHERE id=?",
                      (json.dumps(hashed), secrets.token_hex(16), target['id']))
            c.execute('UPDATE invitations SET used=? WHERE digest=?', (self.state.clock(), self.state.digest(token)))
            c.execute('DELETE FROM sessions WHERE user_id=?', (target['id'],))
            self.event(c, target['id'], target['id'], 'activate')
        return self.record(target['id'])

    def manage(self, payload):
        if payload.get('action') in ('create', 'invite'):
            return self.issue(payload)
        if payload.get('action') not in ('disable', 'enable'):
            raise ValueError('无效的账号操作')
        uid = payload.get('id')
        with self.state.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
            if not row or row['role'] != 'member':
                raise ValueError('不能停用管理员或体验账号')
            status = 'disabled' if payload['action'] == 'disable' else ('active' if row['password'] else 'invited')
            c.execute('UPDATE users SET status=?,generation=? WHERE id=?', (status, secrets.token_hex(16), uid))
            c.execute('DELETE FROM sessions WHERE user_id=?', (uid,))
            c.execute('DELETE FROM invitations WHERE user_id=?', (uid,))
            self.event(c, 'owner', uid, payload['action'])
        return {'user': self.identity_or_invited(uid)}

    def settings(self, uid, patch):
        if uid == 'demo':
            raise ValueError('体验空间不保存设置')
        if not isinstance(patch, dict) or set(patch) - {'theme', 'weather_city', 'navigation_open', 'last_page', 'display_name'}:
            raise ValueError('设置项无效')
        if 'theme' in patch and patch['theme'] not in THEMES:
            raise ValueError('主题无效')
        if 'last_page' in patch and patch['last_page'] not in PAGES:
            raise ValueError('页面无效')
        if 'navigation_open' in patch and not isinstance(patch['navigation_open'], bool):
            raise ValueError('导航设置无效')
        if 'display_name' in patch and (not isinstance(patch['display_name'], str) or not 1 <= len(patch['display_name'].strip()) <= 40):
            raise ValueError('称呼请填写 1—40 字')
        city = patch.get('weather_city')
        if 'weather_city' in patch and (not isinstance(city, dict) or set(city) != {'name', 'latitude', 'longitude'}
                or not isinstance(city.get('name'), str) or not 1 <= len(city['name']) <= 100
                or type(city.get('latitude')) not in (int, float) or not -90 <= city['latitude'] <= 90
                or type(city.get('longitude')) not in (int, float) or not -180 <= city['longitude'] <= 180):
            raise ValueError('城市设置无效')
        with self.state.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT settings FROM users WHERE id=?', (uid,)).fetchone()
            if not row:
                raise ValueError('账号不存在')
            saved = json.loads(row['settings'])
            saved.update({k: v for k, v in patch.items() if k != 'display_name'})
            c.execute('UPDATE users SET settings=? WHERE id=?', (json.dumps(saved, ensure_ascii=False), uid))
            if 'display_name' in patch:
                c.execute('UPDATE users SET display_name=? WHERE id=?', (patch['display_name'].strip(), uid))
        return self.identity(uid)

    def change_password(self, uid, old, new):
        row = self.record(uid)
        if not row or row['role'] in ('demo', 'guest') or not matches(old, row['password']):
            raise ValueError('原密码不正确')
        hashed = password_record(new)
        if uid == 'owner':
            data = self.state.config()
            data.update(password=hashed, generation=secrets.token_hex(16))
            self.state.write_config(data)
        with self.state.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if uid != 'owner':
                c.execute('UPDATE users SET password=?,generation=? WHERE id=?', (json.dumps(hashed), secrets.token_hex(16), uid))
            c.execute('DELETE FROM sessions WHERE user_id=?', (uid,))
            self.event(c, uid, uid, 'password')
