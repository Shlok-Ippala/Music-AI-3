import React from 'react';
import { Play, Square, Circle } from 'lucide-react';
import { clsx } from 'clsx';
import { play, stop, record, getStatus, type ProjectStatus } from '../../services/api';

interface TransportControlsProps {
  isPlaying: boolean;
  isRecording: boolean;
  onProjectChange: (p: ProjectStatus) => void;
}

export default function TransportControls({ isPlaying, isRecording, onProjectChange }: TransportControlsProps) {
  const refresh = async () => {
    try { onProjectChange(await getStatus()); } catch {}
  };

  const handleStop = async () => {
    await stop();
    await refresh();
  };

  const handlePlay = async () => {
    if (isPlaying) {
      await stop();
    } else {
      await play();
    }
    await refresh();
  };

  const handleRecord = async () => {
    await record();
    await refresh();
  };

  return (
    <div className="flex items-center justify-center gap-4 mb-10">
      <button
        onClick={handleStop}
        className="w-12 h-12 rounded-full border border-white/10 bg-white/5 flex items-center justify-center hover:bg-white/10 hover:border-white/20 transition-all hover:scale-[0.98] active:scale-95 text-text-primary"
      >
        <Square className="w-4 h-4" />
      </button>

      <button
        onClick={handlePlay}
        className={clsx(
          "w-16 h-16 rounded-full flex items-center justify-center transition-all hover:scale-[0.98] active:scale-95 shadow-lg",
          isPlaying
            ? "bg-accent-cyan text-bg-main shadow-[0_0_20px_rgba(6,182,212,0.4)]"
            : "bg-accent-violet text-white shadow-[0_0_20px_rgba(124,58,237,0.4)]"
        )}
      >
        <Play className={clsx("w-6 h-6", isPlaying ? "fill-bg-main" : "fill-white ml-1")} />
      </button>

      <button
        onClick={handleRecord}
        className={clsx(
          "w-12 h-12 rounded-full border flex items-center justify-center transition-all hover:scale-[0.98] active:scale-95",
          isRecording
            ? "bg-red-500/20 border-red-500 text-red-500 shadow-[0_0_15px_rgba(239,68,68,0.3)]"
            : "border-white/10 bg-white/5 hover:bg-white/10 hover:border-white/20 text-red-400"
        )}
      >
        <Circle className={clsx("w-4 h-4", isRecording && "fill-red-500")} />
      </button>
    </div>
  );
}
