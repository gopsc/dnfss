#!/usr/bin/env python3
"""
subsystems.db - 数据库子系统

抽离自 aibox.py，提供两个独立的 SQLite 数据库类：
  - MemoryDatabase : 记忆数据库（FTS5 全文检索），存储重要对话记忆
  - MemoDatabase   : 备忘录/任务数据库，支持提醒、重复、即时任务

依赖：common (constants, i18n, Logger) + 标准库 sqlite3
不反向依赖 aibox 任何符号。
"""

import os
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from common import constants, i18n, Logger


# ==================== 记忆数据库 ====================

class MemoryDatabase:
    """记忆数据库 - 存储重要的对话记忆"""

    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = os.path.join(constants.SAVE_DIR, constants.MEMORY_DB)

        # 确保目录存在
        os.makedirs(constants.SAVE_DIR, exist_ok=True)
        self.db_path = db_path
        self.logger = Logger("MemoryDB")
        self._init_database()

    def _init_database(self):
        """初始化数据库表"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    content TEXT,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    tags TEXT,
                    importance INTEGER DEFAULT 1
                )
            ''')

            cursor.execute('''
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
                USING fts5(topic, summary, content, tags, content=memories)
            ''')

            conn.commit()

    def add_memory(self, topic: str, summary: str, content: str = None,
                   start_time: str = None, end_time: str = None,
                   tags: List[str] = None, importance: int = None) -> int:
        """添加记忆到数据库"""
        if start_time is None:
            start_time = datetime.now().strftime(constants.DATETIME_FORMAT)
        if end_time is None:
            end_time = datetime.now().strftime(constants.DATETIME_FORMAT)
        if importance is None:
            importance = constants.MEMORY_DEFAULT_IMPORTANCE

        tags_str = ",".join(tags) if tags else ""
        created_at = datetime.now().strftime(constants.DATETIME_FORMAT)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO memories (topic, summary, content, start_time, end_time, created_at, tags, importance)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (topic, summary, content, start_time, end_time, created_at, tags_str, importance))

            memory_id = cursor.lastrowid

            cursor.execute('''
                INSERT INTO memories_fts (rowid, topic, summary, content, tags)
                VALUES (?, ?, ?, ?, ?)
            ''', (memory_id, topic, summary, content or "", tags_str))

            conn.commit()

        self.logger.info(f"已添加记忆: {topic} (ID: {memory_id})")
        return memory_id

    def delete_memory(self, memory_id: int) -> bool:
        """删除记忆"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM memories WHERE id = ?', (memory_id,))
            deleted = cursor.rowcount > 0

            if deleted:
                cursor.execute('DELETE FROM memories_fts WHERE rowid = ?', (memory_id,))
                conn.commit()
                self.logger.info(f"已删除记忆 ID: {memory_id}")

            return deleted

    def search_memories(self, keywords: List[str], match_all: bool = False, limit: int = None) -> List[Dict]:
        """多关键词模糊搜索记忆"""
        if not keywords:
            return []

        if limit is None:
            limit = constants.MEMORY_MAX_SEARCH_RESULTS

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            if match_all:
                query = " AND ".join([f'"{kw}"' for kw in keywords])
            else:
                query = " OR ".join([f'"{kw}"' for kw in keywords])

            cursor.execute('''
                SELECT m.*, rank
                FROM memories m
                JOIN memories_fts f ON m.rowid = f.rowid
                WHERE memories_fts MATCH ?
                ORDER BY m.end_time DESC, rank, m.importance DESC
                LIMIT ?
            ''', (query, limit))

            results = []
            for row in cursor.fetchall():
                memory = dict(row)
                if memory.get('tags'):
                    memory['tags'] = memory['tags'].split(',')
                results.append(memory)

        return results

    def get_recent_memories(self, limit: int = 10) -> List[Dict]:
        """获取最近的记忆"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM memories ORDER BY end_time DESC LIMIT ?', (limit,))

            results = []
            for row in cursor.fetchall():
                memory = dict(row)
                if memory.get('tags'):
                    memory['tags'] = memory['tags'].split(',')
                results.append(memory)

        return results

    def get_memory_by_id(self, memory_id: int) -> Optional[Dict]:
        """根据ID获取记忆"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM memories WHERE id = ?', (memory_id,))
            row = cursor.fetchone()

            if row:
                memory = dict(row)
                if memory.get('tags'):
                    memory['tags'] = memory['tags'].split(',')
                return memory
        return None

    def delete_memories_by_ids(self, memory_ids: List[int]) -> int:
        """批量删除记忆"""
        if not memory_ids:
            return 0

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            placeholders = ','.join(['?'] * len(memory_ids))
            cursor.execute(f'DELETE FROM memories WHERE id IN ({placeholders})', memory_ids)
            deleted_count = cursor.rowcount

            if deleted_count > 0:
                cursor.execute(f'DELETE FROM memories_fts WHERE rowid IN ({placeholders})', memory_ids)
                conn.commit()
                self.logger.info(f"已批量删除 {deleted_count} 条记忆")

            return deleted_count


# ==================== 备忘录数据库 ====================

class MemoDatabase:
    """备忘录数据库 - 存储和管理任务"""

    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = os.path.join(constants.SAVE_DIR, constants.MEMORY_DB)

        # 确保目录存在
        os.makedirs(constants.SAVE_DIR, exist_ok=True)
        self.db_path = db_path
        self.logger = Logger("MemoDB")
        self._init_database()

    def _init_database(self):
        """初始化备忘录表"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS memos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    content TEXT,
                    created_at TEXT NOT NULL,
                    reminder_time TEXT,
                    completed_at TEXT,
                    is_completed INTEGER DEFAULT 0,
                    tags TEXT,
                    priority INTEGER DEFAULT 1,
                    created_by TEXT,
                    source TEXT,
                    repeat_type TEXT DEFAULT 'none',
                    repeat_interval INTEGER DEFAULT 0,
                    repeat_interval_unit TEXT DEFAULT 'days',
                    repeat_end_time TEXT,
                    last_triggered_at TEXT,
                    trigger_count INTEGER DEFAULT 0,
                    is_immediate INTEGER DEFAULT 0
                )
            ''')

            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_memos_reminder
                ON memos(reminder_time) WHERE is_completed = 0
            ''')

            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_memos_immediate
                ON memos(is_immediate) WHERE is_immediate = 1 AND is_completed = 0
            ''')

            conn.commit()

        self.logger.info("任务表初始化完成")

    def add_memo(self, title: str, content: str = None, reminder_time: str = None,
                 tags: List[str] = None, priority: int = 1, created_by: str = None,
                 source: str = None,
                 repeat_type: str = 'none', repeat_interval: int = 0,
                 repeat_interval_unit: str = 'days', repeat_end_time: str = None,
                 is_immediate: bool = False) -> int:
        """添加任务 - 所有任务都是对话任务，不再需要metadata和任务类型"""
        created_at = datetime.now().strftime(constants.DATETIME_FORMAT)
        tags_str = ",".join(tags) if tags else ""

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO memos
                (title, content, created_at, reminder_time, tags, priority, created_by,
                 is_completed, source, repeat_type, repeat_interval, repeat_interval_unit,
                 repeat_end_time, is_immediate)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
            ''', (title, content, created_at, reminder_time, tags_str, priority, created_by,
                  source, repeat_type, repeat_interval, repeat_interval_unit,
                  repeat_end_time, 1 if is_immediate else 0))

            memo_id = cursor.lastrowid
            conn.commit()

        repeat_info = f" (重复: {repeat_type}"
        if repeat_type == 'custom':
            repeat_info += f", 每{repeat_interval} {repeat_interval_unit}"
        repeat_info += ")"
        immediate_info = " (即时任务)" if is_immediate else ""
        self.logger.info(i18n.get('task_added', title, memo_id) + repeat_info + immediate_info)
        return memo_id

    def get_memo(self, memo_id: int) -> Optional[Dict]:
        """获取单个任务"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM memos WHERE id = ?', (memo_id,))
            row = cursor.fetchone()

            if row:
                memo = dict(row)
                if memo.get('tags'):
                    memo['tags'] = memo['tags'].split(',')
                return memo
        return None

    def get_pending_memos(self, limit: int = None, before_time: str = None, include_immediate: bool = False) -> List[Dict]:
        """获取待执行的任务 - 严格使用当前时间比较"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # 使用当前时间作为基准
            now = datetime.now().strftime(constants.DATETIME_FORMAT)

            # 如果传入了 before_time，使用它，否则使用当前时间
            compare_time = before_time if before_time else now

            query = '''
                SELECT * FROM memos
                WHERE is_completed = 0
                AND reminder_time IS NOT NULL
                AND reminder_time <= ?  -- 严格小于等于比较时间
                AND (repeat_end_time IS NULL OR repeat_end_time >= ?)
            '''

            if not include_immediate:
                query += ' AND is_immediate = 0'

            query += ' ORDER BY reminder_time ASC, priority DESC'

            params = [compare_time, compare_time]

            if limit:
                query += f' LIMIT {limit}'

            cursor.execute(query, params)

            results = []
            for row in cursor.fetchall():
                memo = dict(row)
                if memo.get('tags'):
                    memo['tags'] = memo['tags'].split(',')

                # 【关键修复】二次验证：确保 reminder_time 确实 <= 当前时间
                if memo.get('reminder_time'):
                    try:
                        reminder_time = datetime.strptime(memo['reminder_time'], constants.DATETIME_FORMAT)
                        current_time = datetime.strptime(compare_time, constants.DATETIME_FORMAT)
                        if reminder_time <= current_time:
                            results.append(memo)
                        else:
                            # 调试日志
                            self.logger.debug(f"过滤掉未到期任务 #{memo['id']}: {memo['reminder_time']} > {compare_time}")
                    except (ValueError, TypeError) as e:
                        self.logger.warning(f"解析任务 #{memo['id']} 的提醒时间失败: {e}")
                else:
                    # 没有提醒时间的任务不应该在待执行列表中
                    self.logger.warning(f"任务 #{memo['id']} 没有提醒时间，但被返回为待执行")

            return results

    def get_immediate_memos(self, limit: int = None) -> List[Dict]:
        """获取待执行的即时任务"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            query = '''
                SELECT * FROM memos
                WHERE is_completed = 0
                AND is_immediate = 1
                ORDER BY created_at ASC
            '''

            if limit:
                query += f' LIMIT {limit}'

            cursor.execute(query)

            results = []
            for row in cursor.fetchall():
                memo = dict(row)
                if memo.get('tags'):
                    memo['tags'] = memo['tags'].split(',')
                results.append(memo)

            return results

    def get_overdue_memos(self, limit: int = None) -> List[Dict]:
        """获取已过期的任务"""
        now = datetime.now().strftime(constants.DATETIME_FORMAT)
        return self.get_pending_memos(limit=limit, before_time=now)

    def get_all_memos(self, include_completed: bool = False, limit: int = 50) -> List[Dict]:
        """获取所有任务"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            if include_completed:
                cursor.execute('''
                    SELECT * FROM memos
                    ORDER BY
                        CASE WHEN is_completed = 0 THEN 0 ELSE 1 END,
                        reminder_time ASC,
                        created_at DESC
                    LIMIT ?
                ''', (limit,))
            else:
                cursor.execute('''
                    SELECT * FROM memos
                    WHERE is_completed = 0
                    ORDER BY reminder_time ASC, priority DESC, created_at DESC
                    LIMIT ?
                ''', (limit,))

            results = []
            for row in cursor.fetchall():
                memo = dict(row)
                if memo.get('tags'):
                    memo['tags'] = memo['tags'].split(',')
                results.append(memo)

            return results

    def mark_as_triggered(self, memo_id: int) -> Optional[str]:
        """标记任务已被触发，并根据重复规则计算下一次提醒时间"""
        memo = self.get_memo(memo_id)
        if not memo:
            return None

        now = datetime.now()
        next_reminder = None

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute('''
                UPDATE memos
                SET last_triggered_at = ?, trigger_count = trigger_count + 1
                WHERE id = ?
            ''', (now.strftime(constants.DATETIME_FORMAT), memo_id))

            # 处理重复任务
            if memo['repeat_type'] != 'none' and not self._should_end_repeat(memo, now):
                # 使用当前时间作为基准来计算下一次执行时间
                next_reminder = self._calculate_next_reminder(memo, now)

                if next_reminder:
                    cursor.execute('''
                        UPDATE memos
                        SET reminder_time = ?
                        WHERE id = ?
                    ''', (next_reminder.strftime(constants.DATETIME_FORMAT), memo_id))

                    self.logger.info(f"任务 #{memo_id} 已更新下一次执行时间: {next_reminder}")
                else:
                    # 重复结束，标记为完成
                    cursor.execute('''
                        UPDATE memos
                        SET is_completed = 1, completed_at = ?
                        WHERE id = ?
                    ''', (now.strftime(constants.DATETIME_FORMAT), memo_id))

                    self.logger.info(f"任务 #{memo_id} 重复结束，已标记为完成")
            elif memo['is_immediate']:
                # 即时任务执行后标记为完成
                cursor.execute('''
                    UPDATE memos
                    SET is_completed = 1, completed_at = ?
                    WHERE id = ?
                ''', (now.strftime(constants.DATETIME_FORMAT), memo_id))

                self.logger.info(f"即时任务 #{memo_id} 已完成")
            else:
                # 一次性任务，标记为完成
                cursor.execute('''
                    UPDATE memos
                    SET is_completed = 1, completed_at = ?
                    WHERE id = ?
                ''', (now.strftime(constants.DATETIME_FORMAT), memo_id))

                self.logger.info(f"任务 #{memo_id} 已完成")

            conn.commit()

        return next_reminder.strftime(constants.DATETIME_FORMAT) if next_reminder else None

    def _should_end_repeat(self, memo: Dict, current_time: datetime) -> bool:
        """检查是否应该结束重复"""
        if memo['repeat_end_time']:
            end_time = datetime.strptime(memo['repeat_end_time'], constants.DATETIME_FORMAT)
            if current_time > end_time:
                return True
        return False

    def _calculate_next_reminder(self, memo: Dict, current_time: datetime) -> Optional[datetime]:
        """
        计算下一次提醒时间，使用当前时间作为基准
        支持分钟、小时、天为单位
        """
        if memo['repeat_type'] == 'daily':
            # 每天重复：当前时间 + 1天
            next_time = current_time + timedelta(days=1)
        elif memo['repeat_type'] == 'weekly':
            # 每周重复：当前时间 + 7天
            next_time = current_time + timedelta(weeks=1)
        elif memo['repeat_type'] == 'monthly':
            # 每月重复：尝试在当前时间上加一个月
            try:
                # 处理月份进位
                if current_time.month == 12:
                    next_time = current_time.replace(year=current_time.year + 1, month=1)
                else:
                    next_time = current_time.replace(month=current_time.month + 1)
            except ValueError:
                # 处理月份天数问题（如1月31日加一个月）
                if current_time.month == 12:
                    next_time = current_time.replace(year=current_time.year + 1, month=1, day=1) - timedelta(days=1)
                else:
                    next_time = current_time.replace(month=current_time.month + 1, day=1) - timedelta(days=1)
        elif memo['repeat_type'] == 'custom' and memo['repeat_interval'] > 0:
            unit = memo.get('repeat_interval_unit', 'days')
            if unit == 'minutes':
                # 每N分钟重复：当前时间 + N分钟
                next_time = current_time + timedelta(minutes=memo['repeat_interval'])
            elif unit == 'hours':
                # 每N小时重复：当前时间 + N小时
                next_time = current_time + timedelta(hours=memo['repeat_interval'])
            else:  # days
                # 每N天重复：当前时间 + N天
                next_time = current_time + timedelta(days=memo['repeat_interval'])
        else:
            return None

        # 检查是否超过重复结束时间
        if memo['repeat_end_time']:
            end_time = datetime.strptime(memo['repeat_end_time'], constants.DATETIME_FORMAT)
            if next_time > end_time:
                return None

        return next_time

    def complete_memo(self, memo_id: int, force_complete: bool = False) -> bool:
        """完成任务"""
        memo = self.get_memo(memo_id)
        if not memo:
            return False

        if memo['repeat_type'] != 'none' and not force_complete:
            self.mark_as_triggered(memo_id)
            return True

        completed_at = datetime.now().strftime(constants.DATETIME_FORMAT)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE memos
                SET is_completed = 1, completed_at = ?
                WHERE id = ? AND is_completed = 0
            ''', (completed_at, memo_id))

            affected = cursor.rowcount
            conn.commit()

        if affected > 0:
            self.logger.info(i18n.get('memo_completed', memo_id))
            return True
        return False

    def get_memo_stats(self) -> Dict:
        """获取任务统计信息"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute('SELECT COUNT(*) FROM memos')
            total = cursor.fetchone()[0]

            cursor.execute('SELECT COUNT(*) FROM memos WHERE is_completed = 0')
            pending = cursor.fetchone()[0]

            now = datetime.now().strftime(constants.DATETIME_FORMAT)
            cursor.execute('''
                SELECT COUNT(*) FROM memos
                WHERE is_completed = 0
                AND reminder_time <= ?
                AND is_immediate = 0
            ''', (now,))
            overdue = cursor.fetchone()[0]

            cursor.execute('SELECT COUNT(*) FROM memos WHERE is_completed = 1')
            completed = cursor.fetchone()[0]

            cursor.execute('SELECT COUNT(*) FROM memos WHERE is_immediate = 1 AND is_completed = 0')
            immediate = cursor.fetchone()[0]

            return {
                "total": total,
                "pending": pending,
                "overdue": overdue,
                "completed": completed,
                "immediate": immediate
            }

    def add_user_task(self, title: str, content: str = None, reminder_time: str = None,
                     repeat_type: str = 'none', repeat_interval: int = 0,
                     repeat_interval_unit: str = 'days', is_immediate: bool = False) -> int:
        """添加用户任务（前端直接调用的任务）- 所有任务都是对话任务"""
        if not constants.MEMO_ALLOW_USER_TASKS:
            raise Exception("用户任务功能已禁用")

        return self.add_memo(
            title=title,
            content=content,
            reminder_time=reminder_time,
            repeat_type=repeat_type,
            repeat_interval=repeat_interval,
            repeat_interval_unit=repeat_interval_unit,
            created_by="user",
            source="websocket",
            is_immediate=is_immediate
        )

    def get_pending_tasks_for_user(self) -> List[Dict]:
        """获取待执行任务列表（用于前端显示）"""
        pending = self.get_pending_memos(include_immediate=False)
        immediate = self.get_immediate_memos()
        result = []

        now = datetime.now()
        for memo in pending + immediate:
            result.append({
                "id": memo['id'],
                "title": memo['title'],
                "content": memo['content'],
                "reminder_time": memo['reminder_time'],
                "priority": memo['priority'],
                "is_overdue": datetime.strptime(memo['reminder_time'], constants.DATETIME_FORMAT) < now if memo['reminder_time'] else False,
                "repeat_type": memo['repeat_type'],
                "repeat_interval": memo.get('repeat_interval', 0),
                "repeat_interval_unit": memo.get('repeat_interval_unit', 'days'),
                "trigger_count": memo.get('trigger_count', 0),
                "is_immediate": memo.get('is_immediate', False)
            })

        return result
