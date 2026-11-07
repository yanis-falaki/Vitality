//! The command line: `vitality-client status | pause [MINUTES] | resume | doctor`.
//!
//! `main(args)` parses the arguments, does the command and returns the process's exit code
//! (0 done, 1 the command failed, 2 the arguments were wrong).

/// One parsed command.
#[derive(Debug, PartialEq, Eq)]
pub enum Command {
    Status,
    Pause { minutes: Option<u32> },
    Resume,
    Doctor,
}

const USAGE: &str = "usage: vitality-client [status | pause [MINUTES] | resume | doctor]\n\
                     Without arguments it starts the app.";

/// The command named by `args` (the arguments after the program's name), or the reason it is wrong.
pub fn parse(args: &[String]) -> Result<Command, String> {
    let words: Vec<&str> = args.iter().map(String::as_str).collect();
    match words.as_slice() {
        ["status"] => Ok(Command::Status),
        ["pause"] => Ok(Command::Pause { minutes: None }),
        ["pause", m] => match m.parse::<u32>() {
            Ok(n) if n > 0 => Ok(Command::Pause { minutes: Some(n) }),
            _ => Err(format!("pause takes a whole number of minutes, not {m:?}")),
        },
        ["resume"] => Ok(Command::Resume),
        ["doctor"] => Ok(Command::Doctor),
        _ => Err(format!("unknown command: {}", words.join(" "))),
    }
}

pub fn main(args: &[String]) -> i32 {
    if matches!(args.first().map(String::as_str), Some("-h" | "--help" | "help")) {
        println!("{USAGE}");
        return 0;
    }
    match parse(args) {
        Ok(command) => run(command),
        Err(why) => {
            eprintln!("vitality-client: {why}\n{USAGE}");
            2
        }
    }
}

fn run(command: Command) -> i32 {
    // Filled in by the control socket (status, pause, resume) and the health checks (doctor).
    eprintln!("vitality-client: {command:?} is not available yet");
    1
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(words: &[&str]) -> Vec<String> {
        words.iter().map(|w| w.to_string()).collect()
    }

    #[test]
    fn parses_each_command() {
        assert_eq!(parse(&args(&["status"])), Ok(Command::Status));
        assert_eq!(parse(&args(&["pause"])), Ok(Command::Pause { minutes: None }));
        assert_eq!(parse(&args(&["pause", "30"])), Ok(Command::Pause { minutes: Some(30) }));
        assert_eq!(parse(&args(&["resume"])), Ok(Command::Resume));
        assert_eq!(parse(&args(&["doctor"])), Ok(Command::Doctor));
    }

    #[test]
    fn rejects_bad_arguments() {
        assert!(parse(&args(&["pause", "0"])).is_err());
        assert!(parse(&args(&["pause", "soon"])).is_err());
        assert!(parse(&args(&["status", "now"])).is_err());
        assert!(parse(&args(&["record"])).is_err());
        assert_eq!(main(&args(&["record"])), 2);
    }
}
