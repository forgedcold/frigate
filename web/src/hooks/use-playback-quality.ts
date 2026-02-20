import { useMemo } from "react";
import { useUserPersistence } from "@/hooks/use-user-persistence";
import { isMobile } from "react-device-detect";

export type PlaybackQuality = "auto" | "full" | "proxy";

export function usePlaybackQuality(): [
  PlaybackQuality,
  (q: PlaybackQuality) => void,
] {
  const [quality, setQuality] = useUserPersistence<PlaybackQuality>(
    "playback-quality",
    "auto",
  );
  return [quality ?? "auto", setQuality];
}

export function useResolvedPlaybackQuality(): string {
  const [quality] = usePlaybackQuality();
  return useMemo(() => {
    if (quality === "auto") {
      return isMobile ? "proxy" : "full";
    }
    return quality;
  }, [quality]);
}
