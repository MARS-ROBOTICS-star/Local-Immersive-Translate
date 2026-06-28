export interface EventInterface {
  name: string;
  params?: Record<string, string | number>;
}

export async function report(_key: string, _events: EventInterface[]) {
  return;
}
