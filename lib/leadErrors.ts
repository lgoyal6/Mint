const SOURCE_LABELS: Record<string, string> = {
  apollo_person: "couldn't verify the person",
  exa_people: "couldn't verify the person",
  apollo_org: "couldn't research the company",
  exa_deep: "couldn't research the company",
  company_resolution: "couldn't identify the company",
};

export function humanizeLeadErrors(errors: string[]): string[] {
  const labels = new Set<string>();
  for (const error of errors) {
    const prefix = (error.split(":")[0] ?? "").trim();
    if (SOURCE_LABELS[prefix]) {
      labels.add(SOURCE_LABELS[prefix]);
    } else if (prefix.startsWith("storage")) {
      labels.add("couldn't save the result");
    } else {
      labels.add(error);
    }
  }
  return [...labels];
}
