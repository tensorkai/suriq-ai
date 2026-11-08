import React from "react";
import { Composition } from "remotion";
import { Intro } from "./Intro";

export const RemotionRoot = () => (
  <Composition
    id="SuriqIntro"
    component={Intro}
    durationInFrames={150}
    fps={30}
    width={1920}
    height={1080}
  />
);
