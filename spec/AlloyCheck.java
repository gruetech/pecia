import edu.mit.csail.sdg.alloy4.A4Reporter;
import edu.mit.csail.sdg.alloy4.ErrorWarning;
import edu.mit.csail.sdg.ast.Command;
import edu.mit.csail.sdg.ast.Module;
import edu.mit.csail.sdg.parser.CompUtil;
import edu.mit.csail.sdg.translator.A4Options;
import edu.mit.csail.sdg.translator.A4Solution;
import edu.mit.csail.sdg.translator.TranslateAlloyToKodkod;
import kodkod.engine.satlab.SATFactory;

/**
 * Headless Alloy assertion checker for Project Axiom.
 *
 * Usage:
 *   java -cp alloy.jar:. AlloyCheck <file.als>
 *
 * Exit codes:
 *   0 — all check assertions passed (UNSAT) and all run predicates are satisfiable (SAT)
 *   1 — one or more assertions found a counterexample, or a run found no instance
 *   2 — parse / IO error
 */
public class AlloyCheck {

    public static void main(String[] args) throws Exception {
        if (args.length < 1) {
            System.err.println("Usage: AlloyCheck <file.als>");
            System.exit(2);
        }

        String path = args[0];

        // Silent reporter: suppress verbose internal logging, surface only warnings.
        A4Reporter reporter = new A4Reporter() {
            @Override
            public void warning(ErrorWarning msg) {
                System.err.println("WARNING: " + msg);
            }
        };

        Module world;
        try {
            world = CompUtil.parseEverything_fromFile(reporter, null, path);
        } catch (Exception e) {
            System.err.println("Parse error in " + path + ": " + e.getMessage());
            System.exit(2);
            return;
        }

        A4Options options = new A4Options();
        // SAT4J is the bundled, dependency-free SAT solver.
        options.solver = SATFactory.DEFAULT;

        int total    = 0;
        int failures = 0;

        for (Command command : world.getAllCommands()) {
            total++;
            System.out.print("  " + command.label + " ... ");
            System.out.flush();

            A4Solution result = TranslateAlloyToKodkod.execute_command(
                reporter,
                world.getAllReachableSigs(),
                command,
                options
            );

            boolean pass;
            String  detail;

            if (command.check) {
                // check assertion: UNSAT = no counterexample = PASS
                pass   = !result.satisfiable();
                detail = pass ? "PASS (no counterexample)" : "FAIL (counterexample found)";
            } else {
                // run predicate: SAT = instance found = PASS
                pass   = result.satisfiable();
                detail = pass ? "PASS (instance found)" : "FAIL (no instance — model may be over-constrained)";
            }

            System.out.println(detail);
            if (!pass) failures++;
        }

        System.out.println();
        System.out.printf("Results: %d/%d passed%n", total - failures, total);

        System.exit(failures > 0 ? 1 : 0);
    }
}
