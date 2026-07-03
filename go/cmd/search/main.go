package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strconv"

	"websearch"

	"websearch/provider"
	"websearch/provider/errs"
)

type SearchResult struct {
	Title   string
	Link    string
	Snippet string
}

func PerformSearch(query string, limit int) (res []SearchResult, err error) {
	// Panic recovery to handle scraper breakages gracefully
	defer func() {
		if r := recover(); r != nil {
			err = fmt.Errorf("Web search temporarily unavailable")
		}
	}()

	p := provider.NewUnofficialDuckDuckGo()
	web := websearch.New(p)

	results, err := web.Search(query, limit)
	if err != nil {
		var ipErr *errs.IPBannedError
		if errors.As(err, &ipErr) {
			return nil, fmt.Errorf("IP Banned")
		}
		return nil, err
	}

	var output []SearchResult
	for _, res := range results {
		output = append(output, SearchResult{
			Title:   res.Title,
			Link:    res.Link.String(),
			Snippet: res.Description,
		})
	}

	return output, nil
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "Usage: meerkat_search <query>[limit]")
		os.Exit(1)
	}

	query := os.Args[1]
	limit := 10
	if len(os.Args) > 2 {
		if l, err := strconv.Atoi(os.Args[2]); err == nil {
			limit = l
		}
	}

	results, err := PerformSearch(query, limit)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Search error: %v\n", err)
		os.Exit(1)
	}

	// Output as JSON to stdout so Python can easily parse it
	json.NewEncoder(os.Stdout).Encode(results)
}