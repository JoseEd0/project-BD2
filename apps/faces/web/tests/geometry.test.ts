import { describe, expect, it } from "vitest";

import { coverTransform, fitWithin, frameRectToView, placeLabel, poseArrowSide } from "../src/camera/geometry";

const FRAME = { width: 640, height: 480 };
const PHONE = { width: 390, height: 844 };

describe("overlay geometry", () => {
  it("scales a landscape frame to cover a portrait phone and crops the sides", () => {
    const { scale, offsetX, offsetY } = coverTransform(FRAME, PHONE);
    expect(scale).toBeCloseTo(844 / 480);
    expect(offsetY).toBe(0);
    expect(offsetX).toBeCloseTo((390 - 640 * scale) / 2);
  });

  it("maps a centered box to the center of the view", () => {
    const box = frameRectToView({ x: 270, y: 190, width: 100, height: 100 }, FRAME, PHONE, false);
    expect(box.x + box.width / 2).toBeCloseTo(PHONE.width / 2);
    expect(box.y + box.height / 2).toBeCloseTo(PHONE.height / 2);
  });

  it("mirrors the box with the front camera preview", () => {
    const rect = { x: 400, y: 100, width: 120, height: 140 };
    const plain = frameRectToView(rect, FRAME, PHONE, false);
    const mirrored = frameRectToView(rect, FRAME, PHONE, true);
    expect(mirrored.width).toBeCloseTo(plain.width);
    expect(mirrored.y).toBeCloseTo(plain.y);
    expect(mirrored.x).toBeCloseTo(PHONE.width - plain.x - plain.width);
  });

  it("keeps the identity when frame and view match", () => {
    expect(frameRectToView({ x: 5, y: 6, width: 7, height: 8 }, FRAME, FRAME, false)).toEqual({ x: 5, y: 6, width: 7, height: 8 });
  });

  it("downscales a frame to its longest side and never enlarges it", () => {
    expect(fitWithin({ width: 1280, height: 720 }, 640)).toEqual({ width: 640, height: 360 });
    expect(fitWithin({ width: 720, height: 1280 }, 640)).toEqual({ width: 360, height: 640 });
    expect(fitWithin({ width: 320, height: 240 }, 640)).toEqual({ width: 320, height: 240 });
  });

  it("puts the label under the box, or above it near the bottom, inside the view", () => {
    const label = { width: 120, height: 28 };
    expect(placeLabel({ x: 100, y: 100, width: 100, height: 100 }, label, PHONE, 6)).toEqual({ x: 90, y: 206, below: true });
    expect(placeLabel({ x: 100, y: 780, width: 100, height: 60 }, label, PHONE, 6)).toEqual({ x: 90, y: 746, below: false });
    expect(placeLabel({ x: -40, y: 10, width: 60, height: 60 }, label, PHONE, 6).x).toBe(0);
    expect(placeLabel({ x: 360, y: 10, width: 60, height: 60 }, label, PHONE, 6).x).toBe(PHONE.width - label.width);
  });

  it("points the turn arrow to the person's side on screen", () => {
    expect(poseArrowSide("front", true)).toBeNull();
    expect(poseArrowSide("left", true)).toBe("left");
    expect(poseArrowSide("right", true)).toBe("right");
    expect(poseArrowSide("left", false)).toBe("right");
    expect(poseArrowSide("right", false)).toBe("left");
  });
});
