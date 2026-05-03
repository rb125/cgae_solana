'use client';

export default function Error({
  error,
}: {
  error: Error & { digest?: string };
}) {
  console.error(error);

  return (
    <div style={{ padding: 20 }}>
      <h1>Runtime Error</h1>
      <pre>{error.message}</pre>
    </div>
  );
}
