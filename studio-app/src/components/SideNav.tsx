type SideNavProps = {
  activePath: string;
};

const links = [
  { href: "/", label: "MAIN", icon: "home" },
  { href: "/image-studio", label: "IMAGE STUDIO", icon: "photo_library" },
  { href: "/video-studio", label: "VIDEO STUDIO", icon: "video_library" },
  { href: "/marketing", label: "MARKETING", icon: "campaign" },
];

export function SideNav({ activePath }: SideNavProps) {
  const normalizedActivePath = activePath.replace(/\/$/, "");
  const normalizedLink = (href: string) => href.replace(/\/$/, "");
  const isActive = (href: string) => {
    const linkPath = normalizedLink(href);
    if (!linkPath) return normalizedActivePath === "";
    return normalizedActivePath === linkPath || normalizedActivePath.startsWith(`${linkPath}/`);
  };

  return (
    <nav className="side-nav" aria-label="Studio navigation">
      {links.map((link) => (
        <a
          key={link.href}
          href={link.href}
          className={`nav-item ${isActive(link.href) ? "active" : ""}`}
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
