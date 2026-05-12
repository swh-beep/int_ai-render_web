type SideNavProps = {
  activePath: string;
};

const links = [
  { href: "/app/", label: "MAIN", icon: "home" },
  { href: "/app/image-studio", label: "IMAGE STUDIO", icon: "photo_library" },
  { href: "/app/video-studio", label: "VIDEO STUDIO", icon: "video_library" },
  { href: "/app/marketing", label: "MARKETING", icon: "campaign" },
];

export function SideNav({ activePath }: SideNavProps) {
  const normalizedActivePath = activePath.replace(/\/$/, "");
  const normalizedLink = (href: string) => href.replace(/\/$/, "");
  return (
    <nav className="side-nav" aria-label="Studio navigation">
      {links.map((link) => (
        <a
          key={link.href}
          href={link.href}
          className={`nav-item ${normalizedActivePath === normalizedLink(link.href) || normalizedActivePath.startsWith(`${normalizedLink(link.href)}/`) ? "active" : ""}`}
          title={link.label}
        >
          <span className="material-symbols-outlined nav-icon" aria-hidden="true">
            {link.icon}
          </span>
          <span className="nav-label">{link.label}</span>
        </a>
      ))}
    </nav>
  );
}
