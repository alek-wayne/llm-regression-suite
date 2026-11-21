const paths = {
  search: <><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4 4" /></>,
  chevron: <path d="m7 10 5 5 5-5" />,
  check: <path d="m5 12 4 4L19 6" />,
  close: <path d="m6 6 12 12M18 6 6 18" />,
  alert: <><path d="M12 8v5m0 3h.01" /><circle cx="12" cy="12" r="9" /></>,
  source: <><path d="M7 3h7l4 4v14H7z" /><path d="M14 3v5h5M10 12h5m-5 4h5" /></>,
  layers: <><path d="m12 4 8 4-8 4-8-4 8-4Zm-8 8 8 4 8-4M4 16l8 4 8-4" /></>,
  link: <><path d="m10 14 4-4m-6 6-1 1a3 3 0 0 1-4-4l4-4a3 3 0 0 1 4.5 0m4.5-1 1-1a3 3 0 0 1 4 4l-4 4a3 3 0 0 1-4.5 0" /></>,
};

export function Icon({ name }: { name: keyof typeof paths }) {
  return (
    <svg className="icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      {paths[name]}
    </svg>
  );
}
