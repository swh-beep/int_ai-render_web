type PlaceholderPageProps = {
  title: string;
  eyebrow: string;
};

export function PlaceholderPage({ title, eyebrow }: PlaceholderPageProps) {
  return (
    <main className="studio-shell compact-shell">
      <section className="marketing-header single-column">
        <div>
          <p className="marketing-kicker">{eyebrow}</p>
          <h1 className="marketing-title">{title}</h1>
          <p className="marketing-copy">
            이번 세션에서는 `/app/marketing`부터 이관합니다. 이 경로는 Vite SPA 라우팅과 병행 제공 확인을 위한
            placeholder입니다.
          </p>
        </div>
      </section>
    </main>
  );
}
