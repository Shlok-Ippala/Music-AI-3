import React from 'react';
import TransportControls from '../workspace/TransportControls';
import TrackList from '../workspace/TrackList';
import ActionPills from '../workspace/ActionPills';
import ReaperEmbed from '../workspace/ReaperEmbed';
import type { ProjectStatus } from '../../services/api';

interface CenterPanelProps {
  isProMode: boolean;
  project: ProjectStatus;
  onProjectChange: (p: ProjectStatus) => void;
  onSendMessage: (content: string) => void;
}

export default function CenterPanel({ isProMode, project, onProjectChange, onSendMessage }: CenterPanelProps) {
  return (
    <div className="flex-1 flex flex-col h-full w-full relative z-10 px-4 sm:px-8 py-8 overflow-y-auto">
      {/* Top Bar */}
      <div className="max-w-5xl mx-auto w-full flex items-end justify-between mb-8 z-10">
        <div>
          <h2 className="text-2xl sm:text-3xl font-bold tracking-tight group flex items-center gap-2 cursor-pointer transition-colors hover:text-white text-white/90">
            Untitled Session
            <span className="opacity-0 group-hover:opacity-100 transition-opacity text-text-secondary text-base">✎</span>
          </h2>
          <div className="flex items-center gap-3 mt-2 text-sm text-text-secondary font-mono">
            <span className="cursor-pointer hover:text-white transition-colors">{project.time_sig} Time</span>
            <span className="w-1.5 h-1.5 rounded-full bg-white/20"></span>
            <span className="cursor-pointer hover:text-white transition-colors">{project.track_count} Tracks</span>
          </div>
        </div>
        <div className="text-right">
          <div className="text-text-secondary text-xs uppercase tracking-widest font-semibold mb-1">Tempo</div>
          <div className="text-4xl sm:text-5xl font-mono text-accent-cyan cursor-pointer hover:text-accent-cyan/80 transition-colors drop-shadow-[0_0_10px_rgba(6,182,212,0.3)]">
            {project.tempo ? Math.round(project.tempo) : 120}{' '}
            <span className="text-lg font-sans text-text-secondary uppercase ml-1">BPM</span>
          </div>
        </div>
      </div>

      <TransportControls
        isPlaying={project.playing}
        isRecording={project.recording}
        onProjectChange={onProjectChange}
      />

      {!isProMode ? (
        <>
          <ActionPills onSendMessage={onSendMessage} />
          <TrackList tracks={project.tracks} onProjectChange={onProjectChange} />
        </>
      ) : (
        <ReaperEmbed />
      )}

      <div className="h-32 shrink-0"></div>
    </div>
  );
}
