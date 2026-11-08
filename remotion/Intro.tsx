import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from "remotion";

export const Intro = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const progress = interpolate(frame, [0, fps], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: "#f2f4ef",
        color: "#1b2d25",
        fontFamily: "system-ui, sans-serif",
      }}
    >
      <div
        style={{
          opacity: progress,
          transform: `translateY(${(1 - progress) * 40}px)`,
          textAlign: "center",
        }}
      >
        <h1
          style={{
            margin: 0,
            color: "#235741",
            fontSize: 144,
            fontWeight: 700,
            letterSpacing: -6,
          }}
        >
          SuriQ AI
        </h1>
        <p style={{ margin: "32px 0 0", fontSize: 44 }}>
          Verify supplier quotes against the BOM.
        </p>
      </div>
    </AbsoluteFill>
  );
};
