import React from 'react';
import { clsx } from 'clsx';
import ToolChip from './ToolChip';

type MessageType = 'user' | 'aria';

export interface MessageProps {
  id: string;
  type: MessageType;
  content: string;
  toolCalls?: Array<{ name: string; args: string }>;
  timestamp: string;
  isThinking?: boolean;
}

export default function ChatBubble({ type, content, toolCalls, timestamp, isThinking }: MessageProps) {
  const isUser = type === 'user';

  return (
    <div className={clsx("flex flex-col group", isUser ? "items-end" : "items-start")}>
      {!isUser && toolCalls && toolCalls.length > 0 && (
        <div className="flex flex-col gap-1.5 mb-2 ml-5">
          {toolCalls.map((tool, idx) => (
            <ToolChip key={idx} toolName={tool.name} args={tool.args} delayIndex={idx} />
          ))}
        </div>
      )}

      <div className={clsx(
        "relative max-w-[85%] flex",
        isUser ? "justify-end" : "justify-start gap-3"
      )}>
        {!isUser && (
          <div className="mt-2 w-2 h-2 rounded-full bg-accent-violet shadow-[0_0_8px_rgba(124,58,237,0.6)] shrink-0"></div>
        )}

        <div
          title={timestamp}
          className={clsx(
            "text-sm leading-relaxed",
            isUser
              ? "bg-[#2A2A30] text-white px-4 py-2.5 rounded-2xl rounded-tr-sm border border-white/5 shadow-sm"
              : "text-text-primary py-1"
          )}
        >
          {isThinking && !content ? (
            <span className="flex gap-1 items-center py-1">
              <span className="w-1.5 h-1.5 rounded-full bg-text-secondary animate-bounce" style={{ animationDelay: '0ms' }} />
              <span className="w-1.5 h-1.5 rounded-full bg-text-secondary animate-bounce" style={{ animationDelay: '150ms' }} />
              <span className="w-1.5 h-1.5 rounded-full bg-text-secondary animate-bounce" style={{ animationDelay: '300ms' }} />
            </span>
          ) : content}
        </div>
      </div>

      <span className={clsx(
        "text-[10px] text-text-secondary mt-1 opacity-0 group-hover:opacity-100 transition-opacity px-1",
        !isUser && "ml-5"
      )}>
        {timestamp}
      </span>
    </div>
  );
}
