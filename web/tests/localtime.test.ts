import { describe, expect, it } from "vitest";

import { zagrebDayHour, zagrebOffsetHours } from "../src/localtime";

const zagreb = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Europe/Zagreb",
  hour: "2-digit",
  hourCycle: "h23",
});

describe("Zagreb wall-clock time", () => {
  it("matches the platform time zone database for every hour of 2020 and 2021", () => {
    const start = Date.UTC(2020, 0, 1) / 1000 + 600;
    for (let i = 0; i < 2 * 8784; i++) {
      const t = start + i * 3600;
      const expected = Number(zagreb.format(new Date(t * 1000)));
      const utcHour = new Date(t * 1000).getUTCHours();
      expect((utcHour + zagrebOffsetHours(t)) % 24).toBe(expected);
    }
  });

  it("places the first PVGIS hour of 2020 at 01:10 local on 1 January", () => {
    const t = Date.UTC(2020, 0, 1, 0, 10) / 1000;
    expect(zagrebDayHour(t, 2020)).toEqual({ day: 0, hour: 1 });
  });

  it("puts noon on 21 June at day 172, hour 12, in summer time", () => {
    const t = Date.UTC(2020, 5, 21, 10, 10) / 1000;
    expect(zagrebDayHour(t, 2020)).toEqual({ day: 172, hour: 12 });
  });
});
