#include "nia_ls.h"

#include <cstdint>
#include <fstream>
#include <iostream>
#include <set>
#include <sstream>
#include <string>
#include <vector>

namespace {

std::string trim(const std::string& text) {
    const std::string whitespace = " \t\r\n";
    const std::size_t begin = text.find_first_not_of(whitespace);
    if (begin == std::string::npos) {
        return "";
    }

    const std::size_t end = text.find_last_not_of(whitespace);
    return text.substr(begin, end - begin + 1);
}

std::string parse_objective_name(const std::string& line) {
    const std::string cleaned = trim(line);
    if (cleaned.size() < 2 || cleaned.front() != '(') {
        return "";
    }

    std::istringstream iss(cleaned.substr(1));
    std::string name;
    iss >> name;
    if (name.empty() || name == "objectives" || name == "interval") {
        return "";
    }
    return name;
}

std::string load_problem_from_file(nia::ls_solver& solver, const std::string& file_name) {
    std::ifstream fin(file_name);
    if (!fin) {
        throw std::runtime_error("failed to open input file: " + file_name);
    }

    uint64_t num_lits = 0;
    if (!(fin >> num_lits)) {
        throw std::runtime_error("failed to read literal count from: " + file_name);
    }

    solver.make_lits_space(num_lits + 1);

    std::string line;
    std::getline(fin, line);
    while (std::getline(fin, line)) {
        if (line == "0") {
            break;
        }
        if (line.empty()) {
            continue;
        }
        solver.build_lits(line);
    }

    int clause_count = 0;
    if (!(fin >> clause_count)) {
        throw std::runtime_error("failed to read clause count from: " + file_name);
    }

    std::vector<std::vector<int>> clause_vec(clause_count);
    int clause_idx = 0;
    std::string token;
    while (clause_idx < clause_count && (fin >> token)) {
        if (token == "(") {
            continue;
        }
        if (token == ")") {
            ++clause_idx;
            continue;
        }
        clause_vec[clause_idx].push_back(std::atoi(token.c_str()));
    }

    if (clause_idx != clause_count) {
        throw std::runtime_error("clause data is incomplete in: " + file_name);
    }

    solver.build_instance(clause_vec);
    solver.print_formula();

    bool in_objectives = false;
    while (std::getline(fin, line)) {
        const std::string cleaned = trim(line);
        if (cleaned.empty()) {
            continue;
        }
        if (cleaned == "(objectives") {
            in_objectives = true;
            continue;
        }
        if (!in_objectives) {
            continue;
        }
        if (cleaned == ")") {
            break;
        }
        const std::string objective_name = parse_objective_name(cleaned);
        if (!objective_name.empty()) {
            return objective_name;
        }
    }

    return "";
}

std::vector<std::string> collect_all_variable_names(nia::ls_solver& solver) {
    std::set<std::string> names;
    for (const auto& kv : solver.name2tmp_var) {
        names.insert(kv.first);
    }
    for (const auto& kv : solver.name2resolution_var) {
        names.insert(kv.first);
    }
    for (const auto& kv : solver.name2var) {
        names.insert(kv.first);
    }
    return std::vector<std::string>(names.begin(), names.end());
}

}  // namespace

int main(int argc, char* argv[]) {
    std::string input_path = "./a.txt";
    int seed = 1;
    std::uint64_t max_step = 10000;

    if (argc >= 2) {
        const std::string arg1 = argv[1];
        if (arg1 == "--help" || arg1 == "-h") {
            return 0;
        }
        input_path = arg1;
    }
    if (argc >= 3) {
        seed = std::stoi(argv[2]);
    }
    if (argc >= 4) {
        max_step = static_cast<std::uint64_t>(std::stoull(argv[3]));
    }
    if (argc >= 5) {
        std::cerr << "too many arguments\n";
        return 1;
    }

    try {
        nia::ls_solver solver(seed, max_step, false, false);
        const std::string objective_name = load_problem_from_file(solver, input_path);

        std::cout << "source: " << input_path << "\n";

        if (solver.has_unidentified_lits) {
            std::cerr << "warning: formula contains unsupported literals for nia::ls_solver\n";
        }

        if (solver.build_unsat) {
            std::cout << "unsat (detected during build)\n";
            return 0;
        }

        const bool sat = solver.local_search();
        if (!sat) {
            std::cout << "no_model_found_within_local_search_budget\n";
            return 0;
        }

        solver.up_bool_vars();
        if (!objective_name.empty()) {
            std::string query_name = objective_name;
            std::string objective_value;
            solver.print_var_solution(query_name, objective_value);
            std::cout << "objective(" << objective_name << ") = " << objective_value << "\n";
        }

        std::cout << "sat\n";

        std::cout << "\nreduced-model:\n";
        solver.print_mv();
        return 0;
    } catch (const std::exception& ex) {
        std::cerr << "error: " << ex.what() << "\n";
        return 1;
    }
}