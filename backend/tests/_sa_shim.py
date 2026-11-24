"""仅用于无第三方依赖环境下的集成测试替身。

本地没有 pip / SQLAlchemy（Docker/CI 用真 SQLAlchemy）。这里用标准库 sqlite3
实现被测代码实际用到的 ORM 子集，让 services 层跑在真实 SQLite 库上。
不要在生产代码引用本模块；它只覆盖本项目调用面，不追求完整。
"""

import datetime as _dt
import enum
import json
import sqlite3
from types import GenericAlias


# ---------------------------------------------------------------- 类型与约束

class _SQLType:
    def __init__(self, *args):
        self.args = args


class String(_SQLType):
    pass


class Integer(_SQLType):
    pass


class Text(_SQLType):
    pass


class Boolean(_SQLType):
    pass


class DateTime(_SQLType):
    pass


class JSON(_SQLType):
    pass


class _ForeignKey:
    def __init__(self, reference):
        self.reference = reference


def ForeignKey(reference):
    return _ForeignKey(reference)


class _UniqueConstraint:
    def __init__(self, *columns):
        self.columns = columns


def UniqueConstraint(*columns):
    return _UniqueConstraint(*columns)


class _Index:
    def __init__(self, name, *columns):
        self.name = name
        self.columns = columns


def Index(name, *columns):
    return _Index(name, *columns)


# ---------------------------------------------------------------- 表达式

class _Expression:
    sql = ''
    params = ()


class _OrderBy(_Expression):
    def __init__(self, name, direction):
        self.sql = f'{name} {direction}'
        self.params = []


class _Clause(_Expression):
    def __init__(self, sql, params=()):
        self.sql = sql
        self.params = list(params)

    @staticmethod
    def compare(name, operator, value):
        return _Clause(f'{name} {operator} ?', [value])

    @staticmethod
    def in_clause(name, values):
        values = list(values)
        if not values:
            return _Clause('1 = 0')
        placeholders = ', '.join('?' for _ in values)
        return _Clause(f'{name} IN ({placeholders})', values)

    @staticmethod
    def is_clause(name, value):
        token = 'NULL' if value is None else ('1' if value else '0')
        return _Clause(f'{name} IS {token}')


class _Column:
    """保持默认相等/哈希（按身份），避免破坏模型列字典；比较走显式方法。"""

    def __init__(self, pytype, args, kwargs):
        self.pytype = pytype
        self.args = args
        self.kwargs = kwargs
        self.name = None

    def __eq__(self, other):  # 仅用于 SQL 表达式构造
        return _Clause.compare(self.name, '=', other)

    def __ne__(self, other):
        return _Clause.compare(self.name, '!=', other)

    def __le__(self, other):
        return _Clause.compare(self.name, '<=', other)

    def __ge__(self, other):
        return _Clause.compare(self.name, '>=', other)

    def __lt__(self, other):
        return _Clause.compare(self.name, '<', other)

    def __gt__(self, other):
        return _Clause.compare(self.name, '>', other)

    def __hash__(self):
        return id(self)

    def in_(self, values):
        return _Clause.in_clause(self.name, values)

    def is_(self, value):
        return _Clause.is_clause(self.name, value)

    def desc(self):
        return _OrderBy(self.name, 'DESC')

    def asc(self):
        return _OrderBy(self.name, 'ASC')


def mapped_column(*args, **kwargs):
    annotation = kwargs.pop('__annotation__', None)
    return _Column(annotation, args, kwargs)


class _MappedAlias:
    def __getitem__(self, item):
        return item


Mapped = _MappedAlias()


# ---------------------------------------------------------------- 声明式映射

class _Meta(type):
    def __new__(mcs, name, bases, namespace):
        columns = {}
        for base in bases:
            columns.update(getattr(base, '_columns', {}))
        annotations = namespace.get('__annotations__', {})
        for key, value in list(namespace.items()):
            if isinstance(value, _Column):
                value.name = key
                if value.pytype is None:
                    value.pytype = annotations.get(key)
                columns[key] = value
        namespace['_columns'] = columns
        return super().__new__(mcs, name, bases, namespace)


class DeclarativeBase(metaclass=_Meta):
    __tablename__ = None
    __table_args__ = ()

    def __init__(self, **kwargs):
        for key in self._columns:
            setattr(self, key, kwargs.get(key))
        self._persisted = False


def _is_json(pytype):
    if pytype in (dict, list):
        return True
    origin = getattr(pytype, '__origin__', None)
    if origin in (dict, list):
        return True
    # dict | None 这类联合类型：参数里出现 dict/list 即按 JSON 处理
    args = getattr(pytype, '__args__', None)
    if args and any(arg in (dict, list) or
                    getattr(arg, '__origin__', None) in (dict, list) for arg in args):
        return True
    return False


def _sql_type(column):
    if _is_json(column.pytype):
        return 'TEXT'
    origin = getattr(column.pytype, '__origin__', None) or column.pytype
    if origin is int or any(isinstance(arg, Integer) for arg in column.args):
        return 'INTEGER'
    if origin is bool or any(isinstance(arg, Boolean) for arg in column.args):
        return 'INTEGER'
    return 'TEXT'


def _default_literal(column):
    default = column.kwargs.get('default')
    value = default() if callable(default) else default
    if isinstance(value, bool):
        return '1' if value else '0'
    if isinstance(value, (str, _dt.datetime)):
        return "'" + str(value).replace("'", "''") + "'"
    if value is None:
        return 'NULL'
    return str(value)


def _column_ddl(column):
    pieces = [column.name, _sql_type(column)]
    if column.kwargs.get('primary_key'):
        pieces.append('PRIMARY KEY')
    nullable = column.kwargs.get('nullable', True)
    if not nullable and not column.kwargs.get('primary_key'):
        pieces.append('NOT NULL')
    if 'default' in column.kwargs and not column.kwargs.get('primary_key'):
        pieces.append(f'DEFAULT {_default_literal(column)}')
    return ' '.join(pieces)


class _MetaData:
    def create_all(self, engine):
        connection = engine.connect()
        models = {}

        def collect(cls):
            for subclass in cls.__subclasses__():
                if subclass.__tablename__:
                    models[subclass.__tablename__] = subclass
                collect(subclass)

        collect(DeclarativeBase)
        for table, model in sorted(models.items()):
            definitions = [_column_ddl(column) for column in model._columns.values()]
            for argument in getattr(model, '__table_args__', ()) or ():
                if isinstance(argument, _UniqueConstraint):
                    definitions.append(f'UNIQUE ({", ".join(argument.columns)})')
            connection.execute(
                f'CREATE TABLE IF NOT EXISTS {table} ({", ".join(definitions)})')
        for table, model in sorted(models.items()):
            for argument in getattr(model, '__table_args__', ()) or ():
                if isinstance(argument, _Index):
                    connection.execute(
                        f'CREATE INDEX IF NOT EXISTS {argument.name} '
                        f'ON {table} ({", ".join(argument.columns)})')
        connection.commit()
        connection.close()


DeclarativeBase.metadata = _MetaData()


# ---------------------------------------------------------------- select 构造

class _Select(_Expression):
    def __init__(self, sql, model=None, columns=None):
        self.sql = sql
        self.params = []
        self.model = model
        self.columns = columns or []

    def where(self, *clauses):
        if clauses:
            condition = ' AND '.join(clause.sql for clause in clauses)
            self.sql += f' WHERE {condition}'
            for clause in clauses:
                self.params += clause.params
        return self

    def order_by(self, *clauses):
        rendered = [clause.sql if hasattr(clause, 'sql') else clause.name
                    for clause in clauses]
        self.sql += ' ORDER BY ' + ', '.join(rendered)
        self.params += [p for clause in clauses if hasattr(clause, 'params')
                        for p in clause.params]
        return self

    def limit(self, value):
        self.sql += f' LIMIT {int(value)}'
        return self

    def offset(self, value):
        self.sql += f' OFFSET {int(value)}'
        return self

    def with_for_update(self, skip_locked=False):
        return self

    def outerjoin(self, model, on_clause):
        self.sql += f' LEFT OUTER JOIN {model.__tablename__} ON {on_clause.sql}'
        self.params += on_clause.params
        return self


def _table_of_column(column):
    for model in _all_models():
        if any(column is candidate for candidate in model._columns.values()):
            return model.__tablename__
    return None


def _all_models():
    found = []

    def collect(cls):
        for subclass in cls.__subclasses__():
            if subclass.__tablename__:
                found.append(subclass)
            collect(subclass)

    collect(DeclarativeBase)
    return found


def select(*targets):
    if len(targets) == 1:
        target = targets[0]
        if isinstance(target, type) and issubclass(target, DeclarativeBase):
            return _Select(f'SELECT * FROM {target.__tablename__}', model=target)
        if isinstance(target, _Column):
            if target.name.startswith('COUNT('):
                table = getattr(target, 'table', None) or _table_of_column(target)
                return _Select(f'SELECT {target.name} FROM {table}', columns=[target])
            table = _table_of_column(target)
            base = f'FROM {table} ' if table else ''
            return _Select(f'SELECT {target.name} {base}'.strip(), columns=[target])
        return _Select(f'SELECT {target}')
    names = [getattr(t, 'name', str(t)) for t in targets]
    tables = [_table_of_column(t) for t in targets if isinstance(t, _Column)]
    tables = [name for name in dict.fromkeys(tables) if name]
    from_clause = f'FROM {tables[0]} ' if len(tables) == 1 else ''
    return _Select(('SELECT ' + ', '.join(names) + ' ' + from_clause).strip(),
                   columns=list(targets))


class _CountMarker:
    pass


class _Func:
    def count(self, column):
        marker = _Column(int, (_CountMarker(),), {})
        marker.name = f'COUNT({column.name})'
        marker.table = _table_of_column(column)
        return marker


func = _Func()


class _TextExpr(_Expression):
    def __init__(self, sql):
        self.sql = sql
        self.params = []


def text(sql):
    return _TextExpr(sql)


# ---------------------------------------------------------------- 引擎与会话

class _Engine:
    def __init__(self):
        self.raw = sqlite3.connect(':memory:', check_same_thread=False)

    def connect(self):
        return _Connection(self.raw)

    def begin(self):
        return _Connection(self.raw)


def create_engine(url, **kwargs):
    return _Engine()


class _CursorResult:
    def __init__(self, cursor):
        self._cursor = cursor
    def all(self):
        return self._cursor.fetchall()

    def first(self):
        rows = self._cursor.fetchall()
        return rows[0] if rows else None

    def fetchall(self):
        return self._cursor.fetchall()

    def keys(self):
        return [item[0] for item in self._cursor.description]


def _bind(value):
    if isinstance(value, enum.Enum):
        return value.value
    return value


class _Connection:
    def __init__(self, raw):
        self.raw = raw

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()

    def execute(self, sql, params=()):
        if isinstance(sql, _Expression):
            if not params:
                params = sql.params
            sql = sql.sql
        if isinstance(params, dict):
            cursor = self.raw.execute(sql, {k: _bind(v) for k, v in params.items()})
        else:
            cursor = self.raw.execute(sql, [_bind(value) for value in params])
        return _CursorResult(cursor)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        pass


class _ScalarResult:
    def __init__(self, session, expr):
        self.session = session
        self.expr = expr

    def _result(self):
        return self.session.connection.execute(self.expr.sql, self.expr.params)

    def _rows(self):
        return self._result().fetchall()

    def all(self):
        result = self._result()
        rows = result.fetchall()
        if self.expr.model is not None:
            names = result.keys()
            return [self.session._hydrate(self.expr.model, dict(zip(names, row)))
                    for row in rows]
        values = [row[0] for row in rows]
        columns = getattr(self.expr, 'columns', [])
        if len(columns) == 1 and isinstance(columns[0], _Column) and _is_json(columns[0].pytype):
            return [json.loads(value) if value is not None else None for value in values]
        return values

    def first(self):
        rows = self.all()
        return rows[0] if rows else None

    def __iter__(self):
        return iter(self.all())


class _RowResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


class _Session:
    def __init__(self, engine):
        self.engine = engine
        self.connection = None
        self._pending = []
        self._loaded = {}

    def __enter__(self):
        self.connection = self.engine.connect()
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()

    def begin(self):
        return self.__enter__()

    def commit(self):
        for instance in self._pending:
            self._flush_instance(instance)
        self._pending.clear()
        # 已加载对象的脏更新
        for instance in list(self._loaded.values()):
            if instance._persisted:
                self._update_instance(instance)
        self.connection.commit()

    def rollback(self):
        self._pending.clear()
        self.connection.rollback()

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def add(self, instance):
        self._pending.append(instance)

    def add_all(self, instances):
        self._pending.extend(instances)

    def flush(self):
        for instance in list(self._pending):
            self._flush_instance(instance)
            self._track(instance)
        self._pending.clear()
        for instance in list(self._loaded.values()):
            if instance._persisted:
                self._update_instance(instance)

    @staticmethod
    def _encode(value):
        if isinstance(value, enum.Enum):
            return value.value
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, bool):
            return 1 if value else 0
        if isinstance(value, _dt.datetime):
            return value.isoformat()
        return value

    def _track(self, instance):
        if getattr(instance, 'id', None) is not None:
            self._loaded[(type(instance).__tablename__, instance.id)] = instance

    def _data_snapshot(self, instance):
        data = {}
        for name, column in instance._columns.items():
            value = getattr(instance, name, None)
            if value is None and 'default' in column.kwargs and not column.kwargs.get('primary_key'):
                default = column.kwargs['default']
                value = default() if callable(default) else default
                setattr(instance, name, value)
            if column.kwargs.get('primary_key') and value is None:
                continue
            data[name] = value
        return data

    def _update_instance(self, instance):
        table = instance.__tablename__
        data = self._data_snapshot(instance)
        sets = ', '.join(f'{name} = ?' for name in data)
        self.connection.execute(
            f'UPDATE {table} SET {sets} WHERE id = ?',
            [self._encode(value) for value in data.values()] + [instance.id])

    def _flush_instance(self, instance):
        table = instance.__tablename__
        data = self._data_snapshot(instance)
        if instance._persisted:
            self._update_instance(instance)
            self._track(instance)
            return
        columns = list(data)
        placeholders = ', '.join('?' for _ in columns)
        cursor = self.connection.execute(
            f'INSERT INTO {table} ({", ".join(columns)}) VALUES ({placeholders})',
            [self._encode(data[name]) for name in columns])
        if instance.id is None:
            instance.id = cursor._cursor.lastrowid
        instance._persisted = True
        self._track(instance)

    def get(self, model, primary_key, with_for_update=False):
        result = self.connection.execute(
            f'SELECT * FROM {model.__tablename__} WHERE id = ?', [primary_key])
        rows = result.fetchall()
        if not rows:
            return None
        names = result.keys()
        return self._hydrate(model, dict(zip(names, rows[0])))

    def scalar(self, expr):
        result = self.connection.execute(expr.sql, expr.params)
        rows = result.fetchall()
        if not rows:
            return None
        if getattr(expr, 'model', None) is not None:
            return self._hydrate(expr.model, dict(zip(result.keys(), rows[0])))
        return rows[0][0]

    def scalars(self, expr):
        return _ScalarResult(self, expr)

    def execute(self, expr):
        return _RowResult(self.connection.execute(expr.sql, expr.params).fetchall())

    def _hydrate(self, model, row):
        if not isinstance(row, dict):
            row = dict(zip(model._columns, row))
        values = {}
        for name, column in model._columns.items():
            raw = row.get(name)
            if _is_json(column.pytype) and raw is not None:
                raw = json.loads(raw)
            values[name] = raw
        instance = model(**values)
        instance._persisted = True
        self._track(instance)
        return instance


class _SessionMaker:
    def __init__(self, engine):
        self.engine = engine

    def __call__(self):
        return _Session(self.engine)

    def begin(self):
        session = _Session(self.engine)
        session.__enter__()
        return session


def sessionmaker(engine, **kwargs):
    return _SessionMaker(engine)


# ---------------------------------------------------------------- 反射

class _Inspector:
    def __init__(self, engine):
        self.engine = engine

    def get_table_names(self):
        connection = self.engine.connect()
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        connection.close()
        return [row[0] for row in rows]

    def get_columns(self, table):
        connection = self.engine.connect()
        rows = connection.execute(f'PRAGMA table_info({table})').fetchall()
        connection.close()
        return [{'name': row[1]} for row in rows]


def inspect(engine):
    return _Inspector(engine)
