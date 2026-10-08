import { describe, expect, it } from "vitest";
import { fileName } from "./download";

describe("fileName", () => {
  it("reads plain and encoded names, else the fallback", () => {
    expect(fileName('attachment; filename="tiktok-u-1.mp4"', "x")).toBe("tiktok-u-1.mp4");
    expect(fileName("attachment; filename*=UTF-8''instagram-a%C3%A9-1-sound.mp3", "x")).toBe("instagram-aé-1-sound.mp3");
    expect(fileName(null, "video")).toBe("video");
  });
});
