/**
 * Zagreb wall-clock time, so the carpet and the nine views use the same clock.
 *
 * Croatia follows the EU rule: summer time from 01:00 UTC on the last Sunday of
 * March to 01:00 UTC on the last Sunday of October. The rule is written out
 * rather than asked of Intl for every hour, and tested against Intl.
 */

/** 00:00 UTC on the last Sunday of a month, in seconds. `month` is 0-based. */
function lastSundayUtc(year: number, month: number): number {
  const day = new Date(Date.UTC(year, month + 1, 0));
  day.setUTCDate(day.getUTCDate() - day.getUTCDay());
  return day.getTime() / 1000;
}

/** Hours Zagreb is ahead of UTC at an instant: 1 in winter, 2 in summer. */
export function zagrebOffsetHours(unixSeconds: number): number {
  const year = new Date(unixSeconds * 1000).getUTCFullYear();
  const start = lastSundayUtc(year, 2) + 3600;
  const end = lastSundayUtc(year, 9) + 3600;
  return unixSeconds >= start && unixSeconds < end ? 2 : 1;
}

/** Local calendar position of an instant: day of year from 0, and hour 0 to 23. */
export function zagrebDayHour(unixSeconds: number, year: number): { day: number; hour: number } {
  const local = unixSeconds + zagrebOffsetHours(unixSeconds) * 3600;
  const fromNewYear = local - Date.UTC(year, 0, 1) / 1000;
  return {
    day: Math.floor(fromNewYear / 86400),
    hour: Math.floor((((fromNewYear % 86400) + 86400) % 86400) / 3600),
  };
}
