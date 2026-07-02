package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"math/rand"
	"os"
	"strconv"
	"time"

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
	// 1. Random delay to mitigate rate limiting
	rand.Seed(time.Now().UnixNano())
	delay := 15 + rand.Float64()*30 // 15 to 30 seconds
	time.Sleep(time.Duration(delay * float64(time.Second)))

	// 2. Panic recovery to handle scraper breakages gracefully
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
		fmt.Fprintln(os.Stderr, "Usage: federate_search <query>[limit]")
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