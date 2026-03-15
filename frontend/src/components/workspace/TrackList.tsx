import React, { useState } from 'react';
import { Plus, Volume2, Settings2 } from 'lucide-react';
import { clsx } from 'clsx';
import { addTrack, getStatus, type Track, type ProjectStatus } from '../../services/api';
import AddTrackModal from '../modals/AddTrackModal';

interface TrackListProps {
  tracks: Track[];
  onProjectChange: (p: ProjectStatus) => void;
}

export default function TrackList({ tracks, onProjectChange }: TrackListProps) {
  const [showModal, setShowModal] = useState(false);

  const handleAddTrack = async (type: string, name: string) => {
    await addTrack(type, name);
    try { onProjectChange(await getStatus()); } catch {}
    setShowModal(false);
  };

  return (
    <>
      <div className="flex flex-col gap-3 max-w-4xl mx-auto w-full mb-8">
        {tracks.length === 0 && (
          <div className="text-center py-12 text-text-secondary text-sm">
            No tracks yet. Ask Aria to create something, or add a track below.
          </div>
        )}

        {tracks.map(track => (
          <div
            key={track.index}
            className="group relative flex items-center gap-4 bg-bg-panelLight/40 hover:bg-bg-panel/80 border border-white/5 hover:border-white/10 rounded-xl p-3 pr-4 transition-all hover:-translate-y-[1px] hover:shadow-lg backdrop-blur-sm overflow-hidden"
          >
            <div
              className="absolute left-0 top-0 bottom-0 w-1 transition-all group-hover:w-1.5 opacity-80 group-hover:opacity-100"
              style={{ backgroundColor: track.color, boxShadow: `0 0 10px ${track.color}` }}
            ></div>

            <div className="pl-3 w-40 shrink-0">
              <h3 className="font-semibold text-sm truncate">{track.name}</h3>
              <span className="text-[10px] text-text-secondary uppercase font-mono tracking-wider">{track.type}</span>
            </div>

            <div className="flex items-center gap-2">
              <button className={clsx(
                "w-8 h-8 rounded-md flex items-center justify-center text-xs font-bold transition-colors",
                track.muted
                  ? "bg-red-500/20 text-red-500 border border-red-500/30"
                  : "bg-black/40 text-text-secondary hover:text-white border border-white/5"
              )}>M</button>
              <button className={clsx(
                "w-8 h-8 rounded-md flex items-center justify-center text-xs font-bold transition-colors",
                track.solo
                  ? "bg-yellow-500/20 text-yellow-500 border border-yellow-500/30"
                  : "bg-black/40 text-text-secondary hover:text-white border border-white/5"
              )}>S</button>
            </div>

            <div className="flex items-center gap-3 w-32 shrink-0">
              <Volume2 className="w-4 h-4 text-text-secondary" />
              <div className="h-1.5 flex-1 bg-black/50 rounded-full overflow-hidden">
                <div className="h-full bg-white/80 rounded-full transition-all" style={{ width: `${track.volume}%` }}></div>
              </div>
            </div>

            <div className="flex-1 flex items-center justify-center h-10 bg-black/30 rounded-lg mx-4 border border-white/5 relative overflow-hidden group-hover:bg-black/40 transition-colors">
              <div className="absolute inset-y-0 left-0 right-0 flex items-center px-2 opacity-30 gap-0.5">
                {Array.from({ length: 40 }).map((_, i) => (
                  <div key={i} className="w-1 bg-white rounded-full" style={{ height: `${Math.random() * 80 + 10}%`, opacity: 0.4 }}></div>
                ))}
              </div>
            </div>

            <div className="flex items-center gap-2 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
              <button className="p-2 hover:bg-white/10 rounded-md text-text-secondary hover:text-white transition-colors">
                <Settings2 className="w-4 h-4" />
              </button>
            </div>
          </div>
        ))}

        <button
          onClick={() => setShowModal(true)}
          className="mt-2 w-full max-w-4xl mx-auto h-12 border border-dashed border-white/10 hover:border-white/30 rounded-xl flex items-center justify-center gap-2 text-text-secondary sm:text-sm text-xs font-medium hover:text-white hover:bg-white/5 transition-all outline-none focus:ring-1 focus:ring-accent-violet"
        >
          <Plus className="w-4 h-4" />
          Add Track
        </button>
      </div>

      {showModal && (
        <AddTrackModal onAdd={handleAddTrack} onClose={() => setShowModal(false)} />
      )}
    </>
  );
}
