(function (root, factory) {
  'use strict';
  const calendar = factory();
  if (typeof module === 'object' && module.exports) module.exports = calendar;
  else root.TodoCalendar = calendar;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const DAY = 86400000;
  const weekdays = ['日', '一', '二', '三', '四', '五', '六'];
  const pad = number => String(number).padStart(2, '0');
  const order = (a, b) => a === b ? 0 : a < b ? -1 : 1;

  function utcDate(year, month, day) {
    // Date.UTC(0..99, ...) silently adds 1900; setUTCFullYear does not.
    const result = new Date(0);
    result.setUTCFullYear(year, month - 1, day);
    result.setUTCHours(0, 0, 0, 0);
    return result;
  }

  function dateParts(value) {
    if (typeof value !== 'string') return null;
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
    if (!match) return null;
    const year = Number(match[1]), month = Number(match[2]), day = Number(match[3]);
    if (year < 1 || month < 1 || month > 12 || day < 1 || day > 31) return null;
    const date = utcDate(year, month, day);
    if (date.getUTCFullYear() !== year || date.getUTCMonth() + 1 !== month || date.getUTCDate() !== day) return null;
    return {year, month, day, date};
  }

  function monthParts(value) {
    const parts = dateParts(typeof value === 'string' ? value + '-01' : '');
    if (!parts) throw new RangeError('月份格式应为有效的 YYYY-MM');
    return parts;
  }

  function dateString(date) {
    const year = date.getUTCFullYear();
    if (year < 1 || year > 9999) throw new RangeError('日期超出支持范围');
    return String(year).padStart(4, '0') + '-' + pad(date.getUTCMonth() + 1) + '-' + pad(date.getUTCDate());
  }

  function normalizedTime(value) {
    if (typeof value !== 'string') return '';
    const match = /^(\d{1,2})[:：](\d{2})$/.exec(value.trim());
    if (!match || Number(match[1]) > 23 || Number(match[2]) > 59) return '';
    return pad(Number(match[1])) + ':' + match[2];
  }

  function clockTime(period, hour, minute) {
    hour = Number(hour); minute = Number(minute);
    if (hour > 23 || minute > 59) return '';
    if (period === '上午' && hour === 12) hour = 0;
    else if (period === '下午' && hour > 0 && hour < 12) hour += 12;
    else if (period === '晚上' && hour === 12) hour = 0;
    else if (period === '晚上' && hour > 0 && hour < 12) hour += 12;
    // Noon commonly spans 11:xx–14:xx; midnight keeps the chosen date.
    else if (period === '中午' && hour >= 1 && hour <= 2) hour += 12;
    return pad(hour) + ':' + pad(minute);
  }

  function timeOf(task) {
    if (!task || typeof task !== 'object') return '';
    const explicit = normalizedTime(task.due_time);
    if (explicit) return explicit;
    if (typeof task.title !== 'string') return '';
    // Only clock notation in the title is a fallback. Never parse the note,
    // "tomorrow", "after lunch", Chinese number words, or a guessed deadline.
    const pattern = /(?:^|[^\d:：])(?:(上午|下午|晚上|中午)\s*)?(\d{1,2})[:：](\d{2})(?![\d:：])/g;
    let match;
    while ((match = pattern.exec(task.title))) {
      const time = clockTime(match[1], match[2], match[3]);
      if (time) return time;
    }
    return '';
  }

  function displayParts(task) {
    task = task && typeof task === 'object' ? task : {};
    const title = typeof task.title === 'string' ? task.title : '';
    const explicit = normalizedTime(task.due_time);
    // Only a leading schedule can be moved out of the title. Keep arbitrary
    // body clocks, multiple appointments and conflicting explicit times intact.
    const prefix = /^\s*((?:周|星期|礼拜)([一二三四五六日天])\s*)?((?:(上午|下午|晚上|中午)\s*)?(\d{1,2})[:：](\d{2})(?![\d:：]))(?:\s*([-–—~～至到])\s*((?:(上午|下午|晚上|中午)\s*)?(\d{1,2})[:：](\d{2})(?![\d:：])))?/.exec(title);
    if (!prefix) return {time: explicit, title};
    const start = clockTime(prefix[4], prefix[5], prefix[6]);
    const end = prefix[8] ? clockTime(prefix[9], prefix[10], prefix[11]) : '';
    const remaining = title.slice(prefix[0].length).trimStart();
    if (!start || (prefix[8] && !end) || (explicit && explicit !== start)
        || timeOf({title: remaining})) return {time: explicit, title};

    // Preserve range wording (especially AM/PM and overnight spans) rather
    // than guessing which period an unlabeled end time belongs to.
    const time = prefix[8]
      ? (prefix[3] + prefix[7] + prefix[8]).replaceAll('：', ':')
      : explicit || start;
    const due = dateParts(task.due);
    const weekday = prefix[2] === '天' ? '日' : prefix[2];
    const keepWeekday = prefix[1] && (!due || weekdays[due.date.getUTCDay()] !== weekday);
    return {time, title: (keepWeekday ? prefix[1].trim() + (remaining ? ' ' : '') : '') + remaining};
  }

  function createdKey(value) {
    if (typeof value !== 'string') return [Infinity, 0];
    const match = /^(\d{4}-\d{2}-\d{2})(?:T([01]\d|2[0-3]):([0-5]\d):([0-5]\d)(?:\.(\d{1,9}))?(Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)?)?$/.exec(value);
    if (!match || !dateParts(match[1])) return [Infinity, 0];
    const instant = match[2] ? value + (match[6] ? '' : 'Z') : value + 'T00:00:00Z';
    const milliseconds = Date.parse(instant);
    if (!Number.isFinite(milliseconds)) return [Infinity, 0];
    // Python stores microseconds; retain their order beyond JS millisecond time.
    const remainder = Number((match[5] || '').padEnd(9, '0').slice(3));
    return [milliseconds, remainder];
  }

  function compare(a, b) {
    a = a || {}; b = b || {};
    const dueA = dateParts(a.due) ? a.due : '', dueB = dateParts(b.due) ? b.due : '';
    if (!!dueA !== !!dueB) return dueA ? -1 : 1;
    let result = order(dueA, dueB);
    if (result) return result;
    const importantA = a.priority === 'important', importantB = b.priority === 'important';
    if (importantA !== importantB) return importantA ? -1 : 1;
    const timeA = timeOf(a), timeB = timeOf(b);
    if (!!timeA !== !!timeB) return timeA ? -1 : 1;
    result = order(timeA, timeB);
    if (result) return result;
    const createdA = createdKey(a.created), createdB = createdKey(b.created);
    return order(createdA[0], createdB[0]) || order(createdA[1], createdB[1])
      || order(String(a.id || ''), String(b.id || ''));
  }

  function monthDays(month) {
    const parts = monthParts(month);
    const first = parts.date, mondayOffset = (first.getUTCDay() + 6) % 7;
    const next = utcDate(parts.year, parts.month + 1, 1);
    const days = Math.round((next.getTime() - first.getTime()) / DAY);
    const count = Math.ceil((mondayOffset + days) / 7) * 7;
    const start = first.getTime() - mondayOffset * DAY;
    return Array.from({length: count}, (_, index) => {
      const day = new Date(start + index * DAY);
      return {date: dateString(day), day: day.getUTCDate(),
        inMonth: day.getUTCFullYear() === parts.year && day.getUTCMonth() + 1 === parts.month};
    });
  }

  function weekStart(value) {
    const parts = dateParts(value);
    if (!parts) return '';
    return dateString(new Date(parts.date.getTime() - (parts.date.getUTCDay() + 6) % 7 * DAY));
  }

  function continuousDays(month, padding = 15) {
    const parts = monthParts(month);
    if (!Number.isSafeInteger(padding) || padding < 0 || padding > 366) {
      throw new RangeError('日历前后天数应为 0 至 366 的整数');
    }
    // Include the whole selected month and at least `padding` days on both
    // sides, then extend to complete Monday–Sunday rows. All math is in UTC.
    const before = new Date(parts.date.getTime() - padding * DAY);
    const after = new Date(utcDate(parts.year, parts.month + 1, 1).getTime() + (padding - 1) * DAY);
    const start = before.getTime() - (before.getUTCDay() + 6) % 7 * DAY;
    const end = after.getTime() + (7 - after.getUTCDay()) % 7 * DAY;
    const count = Math.round((end - start) / DAY) + 1;
    return Array.from({length: count}, (_, index) => {
      const day = new Date(start + index * DAY);
      return {date: dateString(day), day: day.getUTCDate(),
        inMonth: day.getUTCFullYear() === parts.year && day.getUTCMonth() + 1 === parts.month};
    });
  }

  function shiftMonth(month, delta) {
    const parts = monthParts(month);
    if (!Number.isSafeInteger(delta)) throw new RangeError('月份偏移必须是整数');
    const absolute = parts.year * 12 + parts.month - 1 + delta;
    const year = Math.floor(absolute / 12), nextMonth = absolute % 12 + 1;
    if (!Number.isSafeInteger(absolute) || year < 1 || year > 9999) throw new RangeError('月份超出支持范围');
    return String(year).padStart(4, '0') + '-' + pad(nextMonth);
  }

  function dayLabel(value, today) {
    const parts = dateParts(value), reference = dateParts(today);
    if (!parts) return '';
    const distance = reference ? (parts.date.getTime() - reference.date.getTime()) / DAY : null;
    const relative = distance === 0 ? '今天' : distance === 1 ? '明天' : distance === -1 ? '昨天' : '';
    const prefix = relative || '星期' + weekdays[parts.date.getUTCDay()];
    const year = !reference || parts.year !== reference.year ? parts.year + '年' : '';
    return prefix + ' ' + year + parts.month + '月' + parts.day + '日';
  }

  return Object.freeze({timeOf, displayParts, compare, monthDays, continuousDays, weekStart, shiftMonth, dayLabel});
});
