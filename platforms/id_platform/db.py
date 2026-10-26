"""轻量线程安全连接池（标准库 queue，零外部依赖）+ 查询辅助。

应用以同步端点 + 线程池方式运行，连接 autocommit，借用时做一次 SELECT 1 健康检查，
失效则重建。需要多语句原子性时，调用方在 connection() 内用 `with conn.transaction():`。
"""
from __future__ import annotations

import contextlib
import queue
import threading
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from . import config


class PoolTimeout(Exception):
    """池满且等待超时：用于对并发洪峰快速失败，而非无限新建连接拖垮 PostgreSQL。"""


class ConnectionPool:
    def __init__(self, dsn: str, minconn: int = 2, maxconn: int = 12,
                 acquire_timeout: float = 10.0) -> None:
        self._dsn = dsn
        self._max = maxconn
        self._acquire_timeout = acquire_timeout
        self._pool: "queue.Queue[psycopg.Connection]" = queue.Queue(maxsize=maxconn)
        # 硬上限：同时在用(借出+空闲)连接数不超过 maxconn。池空时不再无条件新建，
        # 而是阻塞等待空位，超时即抛 PoolTimeout，防止并发洪峰耗尽数据库连接。
        self._slots = threading.BoundedSemaphore(maxconn)
        self._lock = threading.Lock()
        self._created = 0
        for _ in range(max(0, minconn)):
            self._slots.acquire()
            self._pool.put(self._new())

    def _new(self) -> psycopg.Connection:
        with self._lock:
            self._created += 1
        return psycopg.connect(self._dsn, row_factory=dict_row, autocommit=True)

    @contextlib.contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        # 先拿到一个并发额度（总连接数受 _slots 限制）。拿不到就快速失败。
        if not self._slots.acquire(timeout=self._acquire_timeout):
            raise PoolTimeout("数据库连接池繁忙，请稍后重试")
        conn = None
        try:
            try:
                conn = self._pool.get_nowait()
            except queue.Empty:
                conn = self._new()
            # 健康检查：失效则重建
            try:
                if conn.closed:
                    raise psycopg.OperationalError("closed")
                conn.execute("SELECT 1")
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                conn = self._new()
            yield conn
        finally:
            # 归还连接到空闲队列；队列已满则关闭。无论如何释放并发额度。
            if conn is not None:
                try:
                    self._pool.put_nowait(conn)
                except queue.Full:
                    try:
                        conn.close()
                    except Exception:
                        pass
            self._slots.release()


pool = ConnectionPool(config.DB_DSN, config.DB_POOL_MIN, config.DB_POOL_MAX,
                      config.DB_POOL_ACQUIRE_TIMEOUT)

# 成文法库独立连接池（只读详情用途，懒初始化：首次用到时才建立连接，
# 避免在仅用判例功能的部署里强依赖成文法库可达）。
_statutes_pool: ConnectionPool | None = None
_statutes_pool_lock = threading.Lock()


def _get_statutes_pool() -> ConnectionPool:
    global _statutes_pool
    if _statutes_pool is None:
        with _statutes_pool_lock:
            if _statutes_pool is None:
                _statutes_pool = ConnectionPool(
                    config.STATUTES_DB_DSN,
                    config.STATUTES_DB_POOL_MIN,
                    config.STATUTES_DB_POOL_MAX,
                    config.DB_POOL_ACQUIRE_TIMEOUT,
                )
    return _statutes_pool


# 判例库独立连接池（只读详情/核验用途，懒初始化）：get_case_detail 直读判决
# 全文，verification 按 citation_norm 回查。DSN 见 config.CASES_DB_DSN。
_cases_pool: ConnectionPool | None = None
_cases_pool_lock = threading.Lock()


def _get_cases_pool() -> ConnectionPool:
    global _cases_pool
    if _cases_pool is None:
        with _cases_pool_lock:
            if _cases_pool is None:
                _cases_pool = ConnectionPool(
                    config.CASES_DB_DSN,
                    config.CASES_DB_POOL_MIN,
                    config.CASES_DB_POOL_MAX,
                    config.DB_POOL_ACQUIRE_TIMEOUT,
                )
    return _cases_pool


def query_all(sql: str, params: Any = None) -> list[dict]:
    with pool.connection() as conn:
        return conn.execute(sql, params or []).fetchall()


def query_one(sql: str, params: Any = None) -> dict | None:
    with pool.connection() as conn:
        return conn.execute(sql, params or []).fetchone()


def query_all_statutes(sql: str, params: Any = None) -> list[dict]:
    with _get_statutes_pool().connection() as conn:
        return conn.execute(sql, params or []).fetchall()


def query_one_statutes(sql: str, params: Any = None) -> dict | None:
    with _get_statutes_pool().connection() as conn:
        return conn.execute(sql, params or []).fetchone()


def query_all_cases(sql: str, params: Any = None) -> list[dict]:
    with _get_cases_pool().connection() as conn:
        return conn.execute(sql, params or []).fetchall()


def query_one_cases(sql: str, params: Any = None) -> dict | None:
    with _get_cases_pool().connection() as conn:
        return conn.execute(sql, params or []).fetchone()


def execute(sql: str, params: Any = None) -> dict | None:
    """执行写语句；若带 RETURNING 则返回首行，否则 None。"""
    with pool.connection() as conn:
        cur = conn.execute(sql, params or [])
        if cur.description is not None:
            return cur.fetchone()
        return None


def audit(user_id: int | None, action: str, detail: dict | None = None) -> None:
    import json

    try:
        with pool.connection() as conn:
            conn.execute(
                "INSERT INTO app_audit_log (user_id, action, detail) VALUES (%s, %s, %s)",
                [user_id, action, json.dumps(detail or {}, ensure_ascii=False)],
            )
    except Exception:
        # 审计失败不应影响主流程
        pass
