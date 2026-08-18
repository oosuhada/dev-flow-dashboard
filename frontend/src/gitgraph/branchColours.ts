export function createBranchColours() {
  const takenUntil: number[] = [];
  return {
    claim(startAt: number): number {
      const free = takenUntil.findIndex((end) => startAt > end);
      if (free !== -1) return free;
      takenUntil.push(0);
      return takenUntil.length - 1;
    },
    release(colour: number, end: number) {
      takenUntil[colour] = end;
    },
  };
}
